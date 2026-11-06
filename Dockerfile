FROM python:3.13-slim
WORKDIR /app
# código + catálogo (data/: catálogo, disciplinas extintas, materiais, oportunidades). O banco e a chave
# NÃO vão na imagem (.dockerignore): ficam no volume /app/var, que sobrevive a atualizações da imagem.
COPY app.py moderation.py email_login.py ./
COPY scripts ./scripts
COPY static ./static
COPY data ./data
RUN mkdir -p /app/var && chown -R nobody /app/var /app/data
ENV PORT=8000 PYTHONUNBUFFERED=1 DCCHUB_DB=/app/var/dcchub.db
EXPOSE 8000
VOLUME ["/app/var"]
USER nobody
CMD ["python3", "app.py"]
