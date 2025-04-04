import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sw1tch import BASE_DIR, CustomLoggingMiddleware
from sw1tch.routes.public import router as public_router
from sw1tch.routes.admin import router as admin_router
from sw1tch.routes.canary import router as canary_router 

app = FastAPI()
app.add_middleware(CustomLoggingMiddleware)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.include_router(public_router)
app.include_router(admin_router)
app.include_router(canary_router)

if __name__ == "__main__":
    import uvicorn
    from sw1tch import config
    uvicorn.run(
        "sw1tch.__main__:app",  # import string format required for reload
        host="0.0.0.0",
        port=config["port"],
        reload=True,
        access_log=False
    )
