import os
import argparse
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from . import UltraLog

def parse_args():
    parser = argparse.ArgumentParser(description='UltraLog Server')
    parser.add_argument('--log-dir', default='logs', help='Log directory')
    parser.add_argument('--log-file', default='app.log', help='Log filename')
    parser.add_argument('--log-level', default='INFO', help='Logging level')
    parser.add_argument('--name', default='UltraLogger', help='Logger name')
    parser.add_argument('--truncate-file', action='store_true',
                       help='Truncate log file on startup')
    parser.add_argument('--force-sync', action='store_true',
                       help='Force sync log file to disk')
    parser.add_argument('--enable-rotation', action='store_true',
                       help='Enable log rotation')
    parser.add_argument('--batch-size', type=int, default=100,
                       help='Batch size for log messages')
    parser.add_argument('--flush-interval', type=float, default=1.0,
                       help='Flush interval for log messages in seconds')
    parser.add_argument('--file-buffer-size', type=int, default=256*1024,
                       help='Buffer size for file writes in bytes')
    parser.add_argument('--max-file-size', type=int, default=10*1024*1024, 
                       help='Max log file size in bytes')
    parser.add_argument('--backup-count', type=int, default=5,
                       help='Number of backup logs to keep')
    parser.add_argument('--console-output', action='store_true',
                       help='Enable console output')
    parser.add_argument('--auth-token', default='default_token',
                       help='Authentication token for API access')
    parser.add_argument('--host', default='0.0.0.0',
                       help='Host address to bind to')
    parser.add_argument('--port', type=int, default=8000,
                       help='Port number to listen on')
    return parser.parse_args()

args = parse_args()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize logger when FastAPI starts"""
    app.state.logger = UltraLog(
        name=args.name,
        fp=os.path.join(args.log_dir, args.log_file),
        level=args.log_level,
        with_time=True,
        truncate_file=args.truncate_file,
        max_file_size=args.max_file_size,
        backup_count=args.backup_count,
        console_output=args.console_output,
        force_sync=args.force_sync,
        enable_rotation=args.enable_rotation,
        batch_size=args.batch_size,
        flush_interval=args.flush_interval,
        file_buffer_size=args.file_buffer_size,
        server_url=None,  # Local logging
        auth_token=None,  # Local logging
    )
    yield
    app.state.logger.close()

app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

async def verify_token(credentials: HTTPAuthorizationCredentials):
    if credentials.credentials != args.auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials"
        )

@app.post("/log")
async def log_message(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    await verify_token(credentials)
    try:
        data = await request.json()
        level = data.get("level", "INFO").upper()
        message = data.get("message", "")
        
        if level == "DEBUG":
            app.state.logger.debug(message)
        elif level == "INFO":
            app.state.logger.info(message)
        elif level == "WARNING":
            app.state.logger.warning(message)
        elif level == "ERROR":
            app.state.logger.error(message)
        elif level == "CRITICAL":
            app.state.logger.critical(message)
        else:
            app.state.logger.log(message)
            
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        from waitress import serve
        from asgiref.wsgi import WsgiToAsgi
        wsgi_app = WsgiToAsgi(app)
        serve(wsgi_app, host=args.host, port=args.port)
    else:
        import uvicorn
        uvicorn.run(app, host=args.host, port=args.port)
