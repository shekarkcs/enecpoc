FROM python:3.12-slim
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY examples /examples
ENV SAP_MOCK_ITEMS=/examples/sap-items.json DATA_DIR=/data
RUN useradd --uid 10001 app && mkdir /data && chown app /data
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
