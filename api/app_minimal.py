"""
Minimal API version for Vercel deployment with reduced memory footprint.
This version removes heavy dependencies and focuses on core functionality.
"""

import os
import sys
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import logging

# Add the parent directory to the path to import aimakerspace
sys.path.append(str(Path(__file__).parent.parent))

from aimakerspace.text_utils import PDFLoader, WordLoader, CharacterTextSplitter
from aimakerspace.openai_utils.embedding import OpenAIEmbedding
from aimakerspace.openai_utils.chatmodel import ChatOpenAI
from aimakerspace.vectordatabase import VectorDatabase

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Paper Summarizer & Analyzer - Minimal API")

# CORS configuration
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables for document processing
document_chunks: List[str] = []
vector_db: Optional[VectorDatabase] = None
document_sources: List[str] = []

# Initialize components
text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=200)

class RAGChatRequest(BaseModel):
    user_message: str
    model: Optional[str] = "gpt-4o-mini"
    api_key: str
    k: Optional[int] = 8

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    logger.info("Health check endpoint called")
    return {"status": "healthy", "message": "Paper Summarizer & Analyzer API is running"}

@app.post("/api/upload-document")
async def upload_document(
    file: UploadFile = File(...),
    api_key: str = Form(...),
    append_context: bool = Form(False)
):
    """Upload and process PDF or Word document."""
    global document_chunks, vector_db, document_sources
    
    try:
        # Validate file type
        filename = file.filename.lower()
        if not (filename.endswith('.pdf') or filename.endswith('.docx') or filename.endswith('.doc')):
            raise HTTPException(status_code=400, detail="Only PDF and Word documents (.pdf, .docx, .doc) are supported")
        
        # Read file content
        content = await file.read()
        
        # Process document based on file type
        if filename.endswith('.pdf'):
            loader = PDFLoader()
            text_content = loader.load_from_bytes(content)
            doc_type = "PDF"
        else:  # Word document
            loader = WordLoader()
            text_content = loader.load_from_bytes(content)
            doc_type = "Word"
        
        if not text_content:
            raise HTTPException(status_code=400, detail=f"Could not extract text from {doc_type} document")
        
        # Split text into chunks
        chunks = text_splitter.split(text_content)
        
        # Add source tracking
        chunks_with_source = [f"[{doc_type}: {file.filename}] {chunk}" for chunk in chunks]
        
        # Handle context management
        if append_context and document_chunks:
            # Append to existing context
            document_chunks.extend(chunks_with_source)
            document_sources.append(f"{doc_type}: {file.filename}")
        else:
            # Replace existing context
            document_chunks = chunks_with_source
            document_sources = [f"{doc_type}: {file.filename}"]
        
        # Create embeddings and vector database
        embedding_model = OpenAIEmbedding(api_key=api_key)
        vector_db = VectorDatabase(embedding_model)
        
        # Add chunks to vector database
        for i, chunk in enumerate(document_chunks):
            vector_db.add_text(chunk, metadata={"chunk_id": i, "source": document_sources[0]})
        
        # Generate summary
        chat_model = ChatOpenAI(api_key=api_key, model_name="gpt-4o-mini")
        summary_messages = [
            {"role": "system", "content": "You are a helpful assistant that creates concise summaries of documents."},
            {"role": "user", "content": f"Please provide a concise summary (max 100 words) of this {doc_type} document: {chunks[:3]}"}
        ]
        
        summary_response = chat_model.run(summary_messages)
        summary = summary_response.content if hasattr(summary_response, 'content') else str(summary_response)
        
        return {
            "message": f"Successfully uploaded {file.filename}",
            "chunks_count": len(chunks),
            "summary": summary,
            "sources": document_sources,
            "document_type": doc_type
        }
        
    except Exception as e:
        logger.error(f"Error processing document: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing document: {str(e)}")

@app.post("/api/rag-chat-mixed-media")
async def rag_chat(request: RAGChatRequest):
    """RAG chat endpoint for documents (PDF and Word)."""
    global vector_db
    
    try:
        if not vector_db:
            raise HTTPException(status_code=400, detail="No documents uploaded yet")
        
        # Use consistent k value
        k = request.k
        logger.info(f"Using k={k} chunks for search")
        
        # Retrieve relevant chunks
        relevant_chunks = vector_db.search_by_text(
            request.user_message, 
            k=k, 
            return_as_text=True
        )
        
        if not relevant_chunks:
            raise HTTPException(status_code=500, detail="Could not retrieve relevant context")
        
        # Combine context
        context = "\n\n".join(relevant_chunks)
        
        # System message
        system_message = f"""You are a helpful assistant that answers questions based ONLY on the provided context from uploaded content (PDFs and Word documents).

Context from uploaded content:
{context}

Instructions:
- Answer the user's question using ONLY the information provided in the context above
- The context may include content from multiple sources (documents) - each source is labeled
- If the answer cannot be found in the context, say "I cannot find information about that in the provided content"
- Be direct and informative in your responses"""
        
        # Generate response
        chat_model = ChatOpenAI(api_key=request.api_key, model_name=request.model)
        
        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": request.user_message}
        ]
        
        def generate_response():
            try:
                for chunk in chat_model.astream(messages):
                    if hasattr(chunk, 'content') and chunk.content:
                        yield f"data: {chunk.content}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"Error in streaming: {str(e)}")
                yield f"data: Error: {str(e)}\n\n"
        
        return StreamingResponse(
            generate_response(),
            media_type="text/plain",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
        
    except Exception as e:
        logger.error(f"Error in RAG chat endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error in RAG chat: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
