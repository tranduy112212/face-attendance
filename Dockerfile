FROM python:3.10-slim

# Cài full system deps cho dlib
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgl1 \
    libboost-all-dev \
    && rm -rf /var/lib/apt/lists/*

# Pin cmake version an toàn cho dlib
RUN pip install --upgrade pip && pip install cmake==3.25.2

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 10000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000"]
