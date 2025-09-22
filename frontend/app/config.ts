const isDevelopment = process.env.NODE_ENV === 'development';

export const API_URL = isDevelopment 
  ? 'http://localhost:8000'
  : 'https://the-ai-engineer-challenge-m5ndry68n-ashima-manglas-projects.vercel.app/api';