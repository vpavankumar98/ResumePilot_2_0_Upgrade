import io
import os
import re
import time
import zipfile
from datetime import datetime
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
import fitz
from bs4 import BeautifulSoup
from docx import Document
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="ResumePilot 2.0", page_icon="📄", layout="wide")

st.markdown("""
<style>
.block-container {max-width: 1400px; padding-top: 1.5rem;}
.hero {padding: 1.5rem 1.8rem; border-radius: 18px; color: white;
background: linear-gradient(115deg,#14213d,#245be8);}
.job-card {border:1px solid #dce2ee; border-radius:14px; padding:1rem; margin:.7rem 0;}
.muted {color:#667085;font-size:.9rem}
</style>
""", unsafe_allow_html=True)

MAX_JOBS = 500
REQUEST_TIMEOUT = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ResumePilot/2.0; +https://streamlit.io)"}

for key, default in {
    "master_text": "", "master_filename": "", "jobs": [],
    "processed": False, "generated_count": 0
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def extract_file_text(upload):
    data = upload.getvalue()
    name = upload.name.lower()
    if name.endswith(".pdf"):
        pdf = fitz.open(stream=data, filetype="pdf")
        return "\n".join(p.get_text() for p in pdf)
    if name.endswith(".docx"):
        doc = Document(io.BytesIO(data))
        chunks = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                chunks.append(" | ".join(c.text for c in row.cells))
        return "\n".join(chunks)
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore")
    raise ValueError("Supported document types: PDF, DOCX, TXT, MD.")


def extract_urls(upload):
    data = upload.getvalue()
    name = upload.name.lower()
    text = ""
    urls = []
    if name.endswith(".pdf"):
        pdf = fitz.open(stream=data, filetype="pdf")
        text = "\n".join(p.get_text() for p in pdf)
        urls = re.findall(r'https?://[^\s<>"\']+', text)
    elif name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(data), header=None, dtype=str)
        for cell in df.fillna("").values.flatten():
            urls.extend(re.findall(r'https?://[^\s<>"\']+', str(cell)))
    elif name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), header=None, dtype=str, on_bad_lines="skip")
        for cell in df.fillna("").values.flatten():
            urls.extend(re.findall(r'https?://[^\s<>"\']+', str(cell)))
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")
        urls = re.findall(r'https?://[^\s<>"\']+', text)
    else:
        raise ValueError("Upload PDF, XLSX, XLS, CSV or TXT.")
    cleaned = []
    for u in urls:
        u = u.rstrip(".,);]}")
        if u.startswith(("http://", "https://")) and u not in cleaned:
            cleaned.append(u)
    return cleaned


def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip()


def fetch_job_page(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None, "Invalid URL"
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code >= 400:
            return None, f"Website returned HTTP {r.status_code}"
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
            tag.decompose()
        # Prefer common job description containers, otherwise page text.
        selectors = [
            "[data-automation='jobDescription']",
            "[data-testid*='job-description']",
            ".job-description", "#job-description",
            ".description", "main", "article"
        ]
        candidate = ""
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                candidate = node.get_text(" ", strip=True)
                if len(candidate) > 500:
                    break
        if len(candidate) < 300:
            candidate = soup.get_text(" ", strip=True)
        candidate = clean_text(candidate)
        if len(candidate) < 250:
            return None, "Job description not publicly extractable; add JD text manually"
        return candidate[:18000], None
    except requests.RequestException as e:
        return None, f"Could not fetch page: {type(e).__name__}"


STOP = set("""the and for with from that this will your their about into years work team role job using good strong ability experience skills required preferred responsibilities qualifications company position our you are have has not all any can may should must""".split())


def tokens(text):
    return set(re.findall(r"[a-z][a-z0-9+#.-]{1,}", (text or "").lower())) - STOP


def calculate_score(resume, jd):
    rt, jt = tokens(resume), tokens(jd)
    if not jt:
        return None, [], []
    matched = sorted(jt & rt)
    missing = sorted(jt - rt)
    score = round(100 * len(matched) / max(1, len(jt)))
    return score, matched[:40], missing[:40]


def safe_tailor(resume, jd, role, company):
    """Conservative offline tailoring: reorder existing resume lines by JD relevance."""
    lines = [x.strip() for x in resume.splitlines() if x.strip()]
    jd_terms = tokens(jd)
    ranked = []
    for idx, line in enumerate(lines):
        overlap = len(tokens(line) & jd_terms)
        ranked.append((overlap, idx, line))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    ordered = [x[2] for x in ranked]
    header = [
        role.upper() + " | TAILORED RESUME",
        company,
        "",
        "PROFILE AND EXPERIENCE CONTENT REORDERED FOR THIS JOB",
        "The content below is drawn from the candidate's master resume. Verify all details before applying.",
        ""
    ]
    return "\n".join(header + ordered)


def ai_tailor(resume, jd, role, company, api_key):
    if not api_key:
        return safe_tailor(resume, jd, role, company), "Offline tailoring used (no AI API key)."
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = f"""
Tailor the candidate's resume to this job description.
NON-NEGOTIABLE: Never invent or imply employers, job titles, dates, degrees, certifications,
skills, tools, metrics, achievements or responsibilities not explicitly supported by the master resume.
Reorder and rewrite only supported facts. Keep ATS-friendly plain text. Clearly mark any
requirements not evidenced as "GAPS TO REVIEW"; do not claim the candidate has them.
Return a complete resume with clear section headings.
Target role: {role}
Company: {company}
MASTER RESUME:
{resume[:18000]}
JOB DESCRIPTION:
{jd[:12000]}
"""
        response = client.models.generate_content(
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=prompt
        )
        result = (response.text or "").strip()
        if not result:
            raise RuntimeError("AI returned an empty response")
        return result, "AI-tailored draft generated; verify all facts."
    except Exception as e:
        return safe_tailor(resume, jd, role, company), f"AI unavailable; offline tailoring used ({type(e).__name__})."


def make_docx(text, role):
    doc = Document()
    doc.add_heading("Resume", 0)
    doc.add_paragraph(f"Target role: {role}")
    doc.add_paragraph("Review all content for accuracy before submitting.")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if (line.isupper() and len(line) < 80) or line.endswith(":"):
            doc.add_heading(line.rstrip(":"), level=1)
        else:
            doc.add_paragraph(line)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def guess_title(text, url):
    # Use URL path as a fallback title; avoid asserting an unverified title.
    path = urlparse(url).path.strip("/").split("/")[-1]
    guess = re.sub(r"[-_]+", " ", path)
    guess = re.sub(r"\.(html?|aspx?)$", "", guess, flags=re.I)
    return (guess[:75].title() if guess and len(guess) > 2 else "Job title to confirm")


def process_jobs(urls, api_key):
    progress = st.progress(0, text="Starting bulk processing...")
    status = st.empty()
    jobs = []
    total = len(urls)
    for i, url in enumerate(urls, 1):
        status.write(f"Processing job {i} of {total}: {url[:110]}")
        jd, error = fetch_job_page(url)
        title = guess_title(jd or "", url)
        job = {
            "id": i, "url": url, "company": urlparse(url).netloc.replace("www.", ""),
            "role": title, "jd": jd or "", "fetch_error": error,
            "score": None, "matched": [], "missing": [],
            "resume_text": "", "resume_docx": None,
            "tailor_note": "", "status": "Needs JD" if not jd else "Processed"
        }
        if jd and st.session_state.master_text:
            score, matched, missing = calculate_score(st.session_state.master_text, jd)
            job["score"], job["matched"], job["missing"] = score, matched, missing
            tailored, note = ai_tailor(st.session_state.master_text, jd, title, job["company"], api_key)
            job["resume_text"] = tailored
            job["resume_docx"] = make_docx(tailored, title)
            job["tailor_note"] = note
            job["status"] = "Resume ready"
        jobs.append(job)
        progress.progress(i / total, text=f"Processed {i}/{total}")
        # Be polite to external job sites.
        time.sleep(0.15)
    status.success(f"Finished importing {len(jobs)} links. Jobs with inaccessible descriptions need manual JD text.")
    return jobs


st.markdown('<div class="hero"><h1>📄 ResumePilot 2.0</h1><p>Bulk job matching · Automatic resume tailoring · Individual downloads</p><b>Public cloud app · Free-tier starter</b></div>', unsafe_allow_html=True)
st.write("")

with st.sidebar:
    st.header("AI configuration")
    api_key = st.text_input("Gemini API key", value=os.getenv("GEMINI_API_KEY", ""), type="password")
    st.caption("AI calls are subject to the provider's free quota. Without a key, a conservative offline resume reordering draft is generated.")
    st.divider()
    st.caption("Privacy notice: uploaded resumes are processed by this app instance. This starter does not yet provide accounts or private per-user cloud storage. Do not upload sensitive personal data until authentication and storage isolation are added.")

t1, t2, t3 = st.tabs(["① Master Resume", "② Bulk Job Upload", "③ Job Dashboard"])

with t1:
    st.subheader("Upload master resume")
    master = st.file_uploader("PDF or DOCX master resume", type=["pdf", "docx"], key="master")
    if master:
        try:
            txt = extract_file_text(master)
            if txt.strip():
                st.session_state.master_text = txt
                st.session_state.master_filename = master.name
                st.success(f"Master resume loaded: {master.name}")
            else:
                st.error("No selectable text found. Scanned PDFs may need OCR.")
        except Exception as e:
            st.error(str(e))
    if st.session_state.master_text:
        st.info(f"Active master resume: {st.session_state.master_filename}")
        with st.expander("Preview extracted master resume"):
            st.text(st.session_state.master_text[:9000])
        if st.button("Clear master resume"):
            st.session_state.master_text = ""
            st.session_state.master_filename = ""
            st.rerun()

with t2:
    st.subheader("Import up to 500 job URLs")
    st.caption("Supported: PDF, XLSX, XLS, CSV, TXT, or paste links. The file should contain URLs as selectable text/cells.")
    job_file = st.file_uploader("Upload job links file", type=["pdf", "xlsx", "xls", "csv", "txt"], key="joblinks")
    pasted = st.text_area("Or paste multiple job URLs (one per line)", height=130)
    if job_file:
        try:
            preview_urls = extract_urls(job_file)
            st.success(f"Found {len(preview_urls)} unique URLs in {job_file.name}.")
            if preview_urls:
                st.code("\n".join(preview_urls[:15]))
                if len(preview_urls) > 15:
                    st.caption(f"Showing first 15 of {len(preview_urls)} links.")
        except Exception as e:
            preview_urls = []
            st.error(str(e))
    else:
        preview_urls = []
    pasted_urls = re.findall(r'https?://[^\s<>"\']+', pasted)
    all_urls = list(dict.fromkeys(preview_urls + [u.rstrip(".,);]}") for u in pasted_urls]))
    if len(all_urls) > MAX_JOBS:
        st.warning(f"{len(all_urls)} links found. Only the first {MAX_JOBS} will be processed.")
        all_urls = all_urls[:MAX_JOBS]
    st.metric("Unique job links ready", len(all_urls))
    if st.button("Automatically process all jobs", type="primary", use_container_width=True):
        if not st.session_state.master_text:
            st.error("Upload your master resume first.")
        elif not all_urls:
            st.error("Upload a supported file or paste job URLs.")
        else:
            st.session_state.jobs = process_jobs(all_urls, api_key)
            st.session_state.processed = True
            st.success("Bulk processing completed. Open the Job Dashboard tab.")

with t3:
    st.subheader("Job dashboard")
    jobs = st.session_state.jobs
    if not jobs:
        st.info("Upload your master resume and import job links to populate this dashboard.")
    else:
        ready = sum(1 for j in jobs if j["status"] == "Resume ready")
        needs = len(jobs) - ready
        c1, c2, c3 = st.columns(3)
        c1.metric("Total imported", len(jobs))
        c2.metric("Resumes ready", ready)
        c3.metric("Needs JD review", needs)
        min_score = st.slider("Minimum match score", 0, 100, 0)
        sort_mode = st.selectbox("Sort jobs", ["Highest match first", "Lowest match first", "Import order"])
        filtered = [j for j in jobs if j["score"] is None or j["score"] >= min_score]
        if sort_mode == "Highest match first":
            filtered.sort(key=lambda j: j["score"] if j["score"] is not None else -1, reverse=True)
        elif sort_mode == "Lowest match first":
            filtered.sort(key=lambda j: j["score"] if j["score"] is not None else 101)
        for job in filtered:
            with st.container(border=True):
                left, right = st.columns([4, 1])
                with left:
                    st.markdown(f"### {job['role']}")
                    st.caption(job["company"] + " · " + job["status"])
                with right:
                    st.metric("Match score", f"{job['score']}%" if job["score"] is not None else "N/A")
                b1, b2, b3, b4 = st.columns(4)
                with b1:
                    st.link_button("View Job", job["url"], use_container_width=True)
                with b2:
                    st.link_button("Apply Job", job["url"], use_container_width=True)
                with b3:
                    with st.popover("Master Resume", use_container_width=True):
                        st.text(st.session_state.master_text[:7000] or "No master resume uploaded.")
                with b4:
                    if job["resume_docx"]:
                        st.download_button(
                            "Modified Resume",
                            data=job["resume_docx"],
                            file_name=f"ResumePilot_Job_{job['id']}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"download_{job['id']}",
                            use_container_width=True
                        )
                    else:
                        st.button("Modified Resume", disabled=True, key=f"disabled_{job['id']}", use_container_width=True)
                if job["fetch_error"]:
                    st.warning(job["fetch_error"])
                    with st.expander("Add job description manually"):
                        manual_jd = st.text_area("Paste the full JD", key=f"manual_{job['id']}", height=160)
                        if st.button("Analyze and generate resume", key=f"manual_process_{job['id']}"):
                            if manual_jd.strip():
                                score, matched, missing = calculate_score(st.session_state.master_text, manual_jd)
                                tailored, note = ai_tailor(st.session_state.master_text, manual_jd, job["role"], job["company"], api_key)
                                job.update({"jd": manual_jd, "score": score, "matched": matched, "missing": missing,
                                            "resume_text": tailored, "resume_docx": make_docx(tailored, job["role"]),
                                            "tailor_note": note, "status": "Resume ready", "fetch_error": None})
                                st.rerun()
                with st.expander("Match analysis and resume preview"):
                    st.write(f"**Status:** {job['status']}")
                    if job["score"] is not None:
                        st.write(f"**Matched keywords:** {', '.join(job['matched'][:30]) or 'None detected'}")
                        st.write(f"**Potential gaps:** {', '.join(job['missing'][:30]) or 'None detected'}")
                    if job["tailor_note"]:
                        st.caption(job["tailor_note"])
                    if job["resume_text"]:
                        st.text(job["resume_text"][:6000])

st.divider()
st.caption("ResumePilot 2.0 · 500-link import cap. Job-site access, free AI quotas and Streamlit resource limits may affect processing. Match score is a keyword-overlap estimate, not an ATS guarantee.")
