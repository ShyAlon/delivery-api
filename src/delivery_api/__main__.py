def main():
    import os
    import uvicorn
    uvicorn.run("delivery_api.app:app", host=os.getenv("DELIVERY_HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")), reload=False)

if __name__ == "__main__": main()
