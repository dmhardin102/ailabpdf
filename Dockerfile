FROM ghcr.io/prefix-dev/pixi:latest

WORKDIR /app

# Copy pixi.toml first for better layer caching
COPY pixi.toml pixi.lock ./

# Install pixi
RUN pixi install --locked

# Copy application files
COPY . .

# Create uploads directory
RUN mkdir -p uploads

# Expose port 5000 for Flask/Gunicorn
EXPOSE 5000

# Set Flask environment variables (FLASK_APP might not be strictly needed by gunicorn but doesn't hurt)
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Run the application using pixi run gunicorn
CMD ["pixi", "run", "python", "-m", "gunicorn", "--bind", "0.0.0.0:5000", "app:app"]