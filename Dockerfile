# Use official Python slim image for a compact build
#FROM python:3.11-slim
FROM python:3.12-slim

# Set environment variables to optimize Python performance & enforce IST timezone
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Kolkata

# Set the working directory in the container
WORKDIR /app

# Install system-level build tools and tzdata for IST timezone configuration
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    build-essential \
    gcc \
    python3-dev \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file first to utilize Docker's cache layer for dependencies
COPY requirements.txt .

# Upgrade pip and install all Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application files into the container
COPY . .

# Expose Streamlit's default port
EXPOSE 8501

# Default command to run Streamlit dashboard (can be overridden in docker-compose)
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
