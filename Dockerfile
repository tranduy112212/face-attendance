FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Cài CMake phiên bản CŨ (ổn với dlib)
RUN pip install cmake==3.25.2

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .

EXPOSE 10000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000"]
