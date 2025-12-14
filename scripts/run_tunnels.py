import os
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
from pyngrok import ngrok


def main():
    tok = os.getenv("NGROK_AUTHTOKEN")
    if tok:
        try:
            ngrok.set_auth_token(tok)
        except Exception:
            pass
    
    # API (8000)
    api_tunnel = ngrok.connect(8000, bind_tls=True)
    # UI (8501)
    ui_tunnel = ngrok.connect(8501, bind_tls=True)

    print("API:", api_tunnel.public_url)
    print("UI:", ui_tunnel.public_url)

    # Keep alive
    import time
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()


