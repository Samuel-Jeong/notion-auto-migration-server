# Notion Local Backup

## 1) 준비
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# .env 열어 토큰/경로/스케줄 설정
```

## 2) 실행
```bash
uvicorn app.main:app --reload
```
