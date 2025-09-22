# Import required FastAPI components for building the API
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
# Import Pydantic for data validation and settings management
from pydantic import BaseModel, HttpUrl
# Import OpenAI client for interacting with OpenAI's API
from openai import OpenAI
import os
import sys
import tempfile
import asyncio
from pathlib import Path
from typing import Optional, List
import logging
import traceback

# Add the parent directory to sys.path to import aimakerspace
sys.path.append(str(Path(__file__).parent.parent))
from aimakerspace.text_utils import PDFLoader, WordLoader, CharacterTextSplitter
from aimakerspace.video_utils import VideoLoader
from aimakerspace.vectordatabase import VectorDatabase
from aimakerspace.openai_utils.embedding import EmbeddingModel
from aimakerspace.openai_utils.chatmodel import ChatOpenAI

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI application with a title
app = FastAPI(title="RAG-Enabled Chat API")

# Global variables for RAG system
vector_db: Optional[VectorDatabase] = None
document_chunks: List[str] = []
document_sources: List[str] = []  # Track sources of chunks
pdf_uploaded = False

# Get allowed origins from environment variable or use defaults
# For Vercel deployments, allow all vercel.app domains
if os.getenv("VERCEL"):
    ALLOWED_ORIGINS = ["*"]  # Allow all origins in Vercel for simplicity
else:
    ALLOWED_ORIGINS = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000"
    ).split(",")

# Log the allowed origins for debugging
logger.info(f"Allowed origins: {ALLOWED_ORIGINS}")

# Configure CORS (Cross-Origin Resource Sharing) middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Type", "X-Requested-With"],
    max_age=3600,
)

# Define the data model for chat requests using Pydantic
# This ensures incoming request data is properly validated
class ChatRequest(BaseModel):
    developer_message: str  # Message from the developer/system
    user_message: str      # Message from the user
    model: Optional[str] = "gpt-4o-mini"  # Changed to a valid model
    api_key: str          # OpenAI API key for authentication

# Define the data model for document upload options
class DocumentUploadOptions(BaseModel):
    append_context: bool  # Whether to append to existing context or start fresh

# Define the data model for YouTube URL upload
class YouTubeRequest(BaseModel):
    url: HttpUrl          # YouTube video URL
    api_key: str          # OpenAI API key for authentication
    options: DocumentUploadOptions  # Upload options

# Define the data model for RAG chat requests
class RAGChatRequest(BaseModel):
    user_message: str      # Message from the user
    model: Optional[str] = "gpt-4o-mini"  # OpenAI model to use
    api_key: str          # OpenAI API key for authentication
    k: Optional[int] = 8   # Number of relevant chunks to retrieve

# YouTube URL upload endpoint
@app.post("/upload-youtube")
async def upload_youtube(request: YouTubeRequest):
    """Process a YouTube video URL for RAG system."""
    global vector_db, document_chunks, pdf_uploaded
    
    try:
        logger.info(f"Received YouTube URL: {request.url}")
        
        # Validate API key
        if not request.api_key:
            raise HTTPException(status_code=400, detail="API key is required")
        
        # Set the API key for the aimakerspace library
        os.environ["OPENAI_API_KEY"] = request.api_key
        
        try:
            # Process YouTube video
            logger.info("Loading video content...")
            video_loader = VideoLoader(str(request.url))
            video_loader.load_url()
            
            if not video_loader.documents:
                raise HTTPException(status_code=400, detail="Could not extract text from video")
            
            # Split text into chunks
            logger.info("Splitting text into chunks...")
            text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
            new_chunks = text_splitter.split_texts(video_loader.documents)
            
            if not new_chunks:
                raise HTTPException(status_code=400, detail="No text chunks could be created from video")
            
            # Add source information to each chunk
            source_prefix = f"[Source: YouTube - {request.url}]\n"
            new_chunks_with_source = [source_prefix + chunk for chunk in new_chunks]
            
            # Handle append vs replace
            if request.options.append_context and document_chunks:
                document_chunks.extend(new_chunks_with_source)
            else:
                document_chunks = new_chunks_with_source
            
            # Create vector database and embeddings
            logger.info(f"Creating embeddings for {len(document_chunks)} chunks...")
            embedding_model = EmbeddingModel()
            vector_db = VectorDatabase(embedding_model)
            vector_db = await vector_db.abuild_from_list(document_chunks)
            
            # Generate a summary using the video chunks only
            logger.info("Generating video summary...")
            chat_model = ChatOpenAI(model_name="gpt-4o-mini")
            summary_prompt = f"""Provide a concise summary of this video in no more than 100 words. Focus on the key points and main message.

            Video transcript:
            {' '.join(new_chunks_with_source[:5])}
            """
            summary_messages = [
                {"role": "system", "content": "You are a helpful assistant that provides concise video summaries. Always stay within the specified word limit."},
                {"role": "user", "content": summary_prompt}
            ]
            summary_response = chat_model.run(summary_messages)
            
            pdf_uploaded = True
            
            logger.info(f"Successfully processed video with {len(document_chunks)} chunks")
            return {
                "message": f"Video processed successfully. Here's a summary:\n{summary_response}",
                "chunks_count": len(document_chunks),
                "url": str(request.url),
                "summary": summary_response
            }
            
        except Exception as e:
            error_msg = f"Error processing video: {str(e)}"
            logger.error(error_msg)
            raise HTTPException(status_code=500, detail=error_msg)
            
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Error processing video: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=f"Error processing video: {str(e)}")

# PDF upload endpoint for RAG system
@app.post("/upload-document")
async def upload_document(
    file: UploadFile = File(...), 
    api_key: str = Form(...),
    append_context: bool = Form(False)
):
    """Upload and process a PDF or Word document for RAG system."""
    global vector_db, document_chunks, pdf_uploaded
    
    try:
        logger.info(f"Received document upload: {file.filename}")
        
        # Validate file type
        filename = file.filename.lower()
        if not file.filename or not (filename.endswith('.pdf') or filename.endswith('.docx') or filename.endswith('.doc')):
            raise HTTPException(status_code=400, detail="Only PDF and Word documents (.pdf, .docx, .doc) are allowed")
        
        # Validate API key
        if not api_key:
            raise HTTPException(status_code=400, detail="API key is required")
        
        # Set the API key for the aimakerspace library
        os.environ["OPENAI_API_KEY"] = api_key
        
        # Determine file type and extension
        if filename.endswith('.pdf'):
            file_ext = '.pdf'
            doc_type = "PDF"
        elif filename.endswith('.docx'):
            file_ext = '.docx'
            doc_type = "Word"
        else:  # .doc
            file_ext = '.doc'
            doc_type = "Word"
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        try:
            # Process document using aimakerspace
            logger.info(f"Loading {doc_type} content...")
            if doc_type == "PDF":
                loader = PDFLoader(temp_file_path)
            else:  # Word document
                loader = WordLoader(temp_file_path)
            
            loader.load_file()
            
            if not loader.documents:
                raise HTTPException(status_code=400, detail=f"Could not extract text from {doc_type} document")
            
            # Split text into chunks
            logger.info("Splitting text into chunks...")
            logger.info(f"Original document content preview: {loader.documents[0][:300]}...")
            text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
            new_chunks = text_splitter.split_texts(loader.documents)
            logger.info(f"Generated {len(new_chunks)} chunks from document")
            
            if not new_chunks:
                raise HTTPException(status_code=400, detail=f"No text chunks could be created from {doc_type} document")
            
            # Add source information to each chunk
            source_prefix = f"[Source: {doc_type} - {file.filename}]\n"
            new_chunks_with_source = [source_prefix + chunk for chunk in new_chunks]
            
            # Handle append vs replace
            if append_context and document_chunks:
                logger.info("Appending to existing context...")
                document_chunks.extend(new_chunks_with_source)
            else:
                logger.info("Creating new context...")
                document_chunks = new_chunks_with_source
            
            # Create vector database and embeddings
            logger.info(f"Creating embeddings for {len(document_chunks)} chunks...")
            embedding_model = EmbeddingModel()
            vector_db = VectorDatabase(embedding_model)
            vector_db = await vector_db.abuild_from_list(document_chunks)
            
            # Generate a summary using only the newly uploaded document chunks
            logger.info("Generating document summary...")
            chat_model = ChatOpenAI(model_name="gpt-4o-mini")
            
            # Use all the new chunks for summary, not just the first 5
            summary_content = ' '.join(new_chunks_with_source)
            logger.info(f"Number of chunks: {len(new_chunks_with_source)}")
            logger.info(f"Summary content preview (first 500 chars): {summary_content[:500]}...")
            logger.info(f"Summary content length: {len(summary_content)} characters")
            summary_prompt = f"""Provide a concise summary of this {doc_type} document in no more than 100 words. Focus on the key aspects and main contributions.

            Document content:
            {summary_content}
            """
            summary_messages = [
                {"role": "system", "content": "You are a helpful assistant that provides concise document summaries. Always stay within the specified word limit."},
                {"role": "user", "content": summary_prompt}
            ]
            summary_response = chat_model.run(summary_messages)
            logger.info(f"Generated summary: {summary_response}")
            
            pdf_uploaded = True
            
            logger.info(f"Successfully processed {doc_type} document with {len(document_chunks)} chunks")
            return {
                "message": f"{doc_type} document processed successfully. Here's a summary:\n{summary_response}",
                "chunks_count": len(document_chunks),
                "filename": file.filename,
                "summary": summary_response,
                "document_type": doc_type
            }
            
        finally:
            # Clean up temporary file
            os.unlink(temp_file_path)
            
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Error processing document: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=f"Error processing document: {str(e)}")

# RAG-enabled chat endpoint
@app.post("/rag-chat-mixed-media")
async def rag_chat(request: RAGChatRequest):
    """Chat endpoint that uses uploaded documents (PDFs, Word documents) as context."""
    global vector_db, document_chunks, pdf_uploaded
    
    try:
        logger.info(f"Received RAG chat request: {request.user_message[:100]}...")
        
        # Check if PDF has been uploaded
        if not pdf_uploaded or not vector_db:
            raise HTTPException(
                status_code=400, 
                detail="No document has been uploaded. Please upload a document first."
            )
        
        # Set the API key for the aimakerspace library
        os.environ["OPENAI_API_KEY"] = request.api_key
        
        # Use a consistent k value for all queries - keep it simple
        k = request.k  # Default is now 8, which should work well for most questions
        logger.info(f"Using k={k} chunks for search")
        
        # Retrieve relevant chunks from vector database
        logger.info(f"Searching for relevant context with k={k}...")
        logger.info(f"Total document chunks available: {len(document_chunks)}")
        
        relevant_chunks = vector_db.search_by_text(
            request.user_message, 
            k=k, 
            return_as_text=True
        )
        
        logger.info(f"Found {len(relevant_chunks) if relevant_chunks else 0} relevant chunks")
        
        if not relevant_chunks:
            # Fallback: use first few chunks if no relevant chunks found
            logger.warning("No relevant chunks found, using fallback strategy with first 5 chunks")
            relevant_chunks = document_chunks[:5] if len(document_chunks) >= 5 else document_chunks
        
        # If still no chunks, try with a more general search
        if not relevant_chunks and document_chunks:
            logger.warning("Using all available chunks as last resort")
            relevant_chunks = document_chunks[:8]  # Use first 8 chunks
        
        if not relevant_chunks:
            raise HTTPException(status_code=500, detail="Could not retrieve relevant context")
        
        # Combine relevant chunks into context
        context = "\n\n".join(relevant_chunks)
        
        # Create simple system message for all questions
        system_message = f"""You are a helpful assistant that answers questions based ONLY on the provided context from uploaded content (PDFs and Word documents). 

Context from uploaded content:
{context}

Instructions:
- Answer the user's question using ONLY the information provided in the context above
- The context may include content from multiple sources (documents) - each source is labeled
- If the answer cannot be found in the context, say "I cannot find information about that in the provided content"
- Do not use any external knowledge beyond what's in the context
- Be direct and informative in your responses"""

        # Initialize ChatOpenAI and create streaming response
        chat_model = ChatOpenAI(model_name=request.model)
        
        # Create an async generator function for streaming responses
        async def generate():
            try:
                logger.info("Starting RAG chat completion request")
                
                messages = [
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": request.user_message}
                ]
                
                # Use the astream method for streaming
                async for chunk in chat_model.astream(messages):
                    yield chunk
                        
            except Exception as e:
                error_msg = f"Error in RAG generate: {str(e)}\n{traceback.format_exc()}"
                logger.error(error_msg)
                yield f"Error: {str(e)}"

        # Return a streaming response to the client
        return StreamingResponse(generate(), media_type="text/plain")
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Error in RAG chat endpoint: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=str(e))

# Get PDF status endpoint
@app.get("/pdf-status")
async def pdf_status():
    """Get current PDF upload status."""
    global pdf_uploaded, document_chunks
    
    return {
        "pdf_uploaded": pdf_uploaded,
        "chunks_count": len(document_chunks) if document_chunks else 0
    }

# Define the main chat endpoint that handles POST requests
@app.post("/chat")
async def chat(request: ChatRequest):
    try:
        logger.info(f"Received chat request with model: {request.model}")
        logger.info(f"Request headers: {request.headers if hasattr(request, 'headers') else 'No headers'}")
        
        # Initialize OpenAI client with the provided API key
        client = OpenAI(api_key=request.api_key)
        
        # Create an async generator function for streaming responses
        async def generate():
            try:
                logger.info("Starting chat completion request")
                # Create a streaming chat completion request
                stream = client.chat.completions.create(
                    model=request.model,
                    messages=[
                        {"role": "developer", "content": request.developer_message},
                        {"role": "user", "content": request.user_message}
                    ],
                    stream=True  # Enable streaming response
                )
                
                logger.info("Stream created successfully")
                # Yield each chunk of the response as it becomes available
                for chunk in stream:
                    if chunk.choices[0].delta.content is not None:
                        yield chunk.choices[0].delta.content
                        
            except Exception as e:
                error_msg = f"Error in generate: {str(e)}\n{traceback.format_exc()}"
                logger.error(error_msg)
                yield f"Error: {str(e)}"

        # Return a streaming response to the client
        return StreamingResponse(generate(), media_type="text/plain")
    
    except Exception as e:
        error_msg = f"Error in chat endpoint: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=str(e))

# Define a health check endpoint to verify API status
@app.get("/health")
async def health_check():
    try:
        logger.info("Health check endpoint called")
        return {"status": "ok", "message": "API is running"}
    except Exception as e:
        error_msg = f"Error in health check: {str(e)}\n{traceback.format_exc()}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=str(e))

# Entry point for running the application directly
if __name__ == "__main__":
    import uvicorn
    # Start the server on all network interfaces (0.0.0.0) on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
