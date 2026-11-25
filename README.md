# ResumePilot 2.0 – Bulk Job Processing MVP

## What it does
- Upload one master resume (PDF/DOCX).
- Import up to 500 URLs from PDF, XLSX, XLS, CSV, TXT, or paste URLs.
- Attempts to fetch public job descriptions.
- Calculates a keyword-overlap match estimate.
- Automatically creates a separate DOCX draft for each accessible JD.
- Shows View Job, Apply Job, Master Resume, and Modified Resume controls per job.
- Jobs whose pages block extraction can be completed by pasting the JD manually.

## Update the existing public app
1. Download and extract this ZIP.
2. Open your existing GitHub repository (the one connected to your current Streamlit app).
3. Replace the existing `app.py` and `requirements.txt` with the versions in this folder.
4. Commit the changes to the same branch (usually `main`).
5. Streamlit Community Cloud will detect the commit and redeploy the same app URL.

Do not delete the Streamlit app or create a new app. Keep the existing repository and deployment.

## Optional Gemini API
Paste your API key into the app sidebar, or configure it in Streamlit app Secrets as:
GEMINI_API_KEY = "your-key"
GEMINI_MODEL = "gemini-2.5-flash"

API model names, free quotas and terms can change. If no API key is provided, the app uses conservative offline tailoring by reordering existing resume lines. This is not equivalent to generative AI rewriting.

## Limitations to understand
- 500 is the maximum number of URLs accepted in one import, not a promise that 500 pages can be fetched or AI-processed instantly.
- Employer ATS pages may block automated access, require JavaScript, or expose only partial text. The app marks these for manual JD input.
- Free Gemini quotas may not support 500 AI requests in one run. If quota is exceeded, offline tailoring is used for that job.
- This starter keeps data in Streamlit session memory. Jobs and uploaded files are not durably saved across sessions/restarts.
- This version does not yet implement user registration, per-user private storage, database persistence, or production-grade multi-tenant security. Do not invite public users to upload personal resumes until these are added and tested.
- Apply Job opens the job URL; it does not automatically submit employer applications.
- Scores are keyword overlap estimates, not a guaranteed ATS score or hiring probability.
- Always review the generated resume for factual accuracy before applying.
