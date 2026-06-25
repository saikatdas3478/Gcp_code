
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
ROOT_AGENT_MODEL=gemini-2.0-flash-001

SAP_HC_MAX_LINES_PER_FILE=1000
SAP_HC_MAX_PARALLEL_VMS=3
SAP_HC_LLM_TEMPERATURE=0.2
SAP_HC_MAX_OUTPUT_TOKENS=

RAG_DEFAULT_TOP_K=8
RAG_DEFAULT_VECTOR_DISTANCE_THRESHOLD=0.5
SAP_HC_PREVIOUS_REPORTS_TOP_K=5
SAP_HC_MAX_RETRIEVAL_QUERIES_PER_SOURCE=8
SAP_HC_MAX_QUERY_CHARS=600
SAP_HC_RETRIEVAL_MAX_WORKERS=6
SAP_HC_MAX_GOOGLE_DOCS=5
SAP_HC_MAX_GOOGLE_DOC_CHARS=6000
SAP_HC_MIN_RULEBOOK_MATCH_SCORE=0.08
SAP_HC_MAX_FOLDER_VECTOR_HITS=8

fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-dotenv>=1.0.1
pydantic>=2.11.0
google-cloud-storage>=2.18.0
google-cloud-aiplatform[adk,agent-engines]>=1.93.0
google-genai>=1.0.0
google-adk>=1.5.0
requests>=2.32.0
numpy>=2.0.0

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["sh", "-c", "uvicorn sap_hc_agent.main:app --host 0.0.0.0 --port ${PORT}"]
