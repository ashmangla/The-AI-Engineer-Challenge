'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { API_URL } from './config';

export default function Home() {
  const [apiStatus, setApiStatus] = useState<'loading' | 'healthy' | 'error'>('loading');

  useEffect(() => {
    const checkApiHealth = async () => {
      try {
        const response = await fetch(`${API_URL}/health`);
        if (response.ok) {
          setApiStatus('healthy');
        } else {
          setApiStatus('error');
        }
      } catch (error) {
        console.error('Error checking API health:', error);
        setApiStatus('error');
      }
    };

    checkApiHealth();
  }, []);

  return (
    <div className="flex flex-col items-center justify-center min-h-screen p-4">
      <div className="w-full max-w-2xl bg-white/80 backdrop-blur-md rounded-2xl shadow-2xl p-8 border border-blue-100">
        <h1 className="text-4xl font-bold text-blue-700 mb-6">Paper Summarizer & Analyzer</h1>
        
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-gray-600">API Status:</span>
            <span className={`px-2 py-1 rounded text-sm ${
              apiStatus === 'loading' ? 'bg-yellow-100 text-yellow-800' :
              apiStatus === 'healthy' ? 'bg-green-100 text-green-800' :
              'bg-red-100 text-red-800'
            }`}>
              {apiStatus === 'loading' ? 'Checking...' :
               apiStatus === 'healthy' ? 'Healthy' :
               'Error'}
            </span>
          </div>
        </div>

        <div className="space-y-4">
          <Link
            href="/chat"
            className="block w-full p-4 text-center bg-gradient-to-r from-blue-500 to-purple-500 text-white rounded-lg font-bold shadow-md hover:from-blue-600 hover:to-purple-600 transition-all duration-150"
          >
            Start Analyzing
          </Link>
          
          <Link
            href="/about"
            className="block w-full p-4 text-center bg-white text-blue-600 border border-blue-200 rounded-lg font-bold shadow-sm hover:bg-blue-50 transition-all duration-150"
          >
            Learn More
          </Link>
        </div>
      </div>
    </div>
  );
} 