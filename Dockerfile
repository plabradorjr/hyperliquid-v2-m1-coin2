# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set python to unbuffered mode
ENV PYTHONUNBUFFERED=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container at /app
COPY . .

# Define the command to run your app
CMD ["python", "main.py"]
