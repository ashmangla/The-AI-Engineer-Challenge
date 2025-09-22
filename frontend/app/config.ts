const isDevelopment = process.env.NODE_ENV === 'development';

export const API_URL = isDevelopment 
  ? 'http://localhost:8000'
  : 'https://the-ai-engineer-challenge-fxzb7gq5m-ashima-manglas-projects.vercel.app/api';