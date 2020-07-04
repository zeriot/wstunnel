# Wstunnel

A websocket tunnel that proxies TCP and UDP traffic. Written in Python3 with asyncio.

Requires Python 3.8 or higher.

# Usage

## Server

```
usage: server.py [-h] -l IP:PORT -r routes.json [-i IDLE_TIMEOUT] [-s server.pem] [-c ca.pem]
                 [--enable-compress [ENABLE_COMPRESS]] [--log-file FILE]
                 [--log-level {debug,info,error,critical}]

Wstunnel server

optional arguments:
  -h, --help            show this help message and exit
  -l IP:PORT, --listen IP:PORT
                        Listen address
  -r routes.json, --routes routes.json
                        File defining paths and upstream servers
  -i IDLE_TIMEOUT, --idle-timeout IDLE_TIMEOUT
                        Seconds to wait before an idle connection being killed
  -s server.pem, --cert server.pem
                        Server certificate in PEM format with private key
  -c ca.pem, --client-ca ca.pem
                        Client CA certificates in PEM format to verify against
  --enable-compress [ENABLE_COMPRESS]
                        Compress data before sending
  --log-file FILE       Log to FILE
  --log-level {debug,info,error,critical}
                        Log level
```

## Client

```
usage: client.py [-h] --url ws[s]://HOSTNAME:PORT/PATH -l (tcp|udp)://IP:PORT [-p FILE] [-i IDLE_TIMEOUT]
                 [-s ca.pem] [-c client.pem] [--enable-compress [ENABLE_COMPRESS]] [--log-file FILE]
                 [--log-level {debug,info,error,critical}]

Wstunnel client

optional arguments:
  -h, --help            show this help message and exit
  --url ws[s]://HOSTNAME:PORT/PATH
                        URL
  -l (tcp|udp)://IP:PORT, --listen (tcp|udp)://IP:PORT
                        Listen address
  -p FILE, --passwd FILE
                        File containing one line of password to authenticate to the proxy server
  -i IDLE_TIMEOUT, --idle-timeout IDLE_TIMEOUT
                        Seconds to wait before an idle connection being killed
  -s ca.pem, --ca-certs ca.pem
                        Server CA certificates in PEM format to verify against
  -c client.pem, --client-cert client.pem
                        Client certificate in PEM format with private key
  --enable-compress [ENABLE_COMPRESS]
                        Compress data before sending
  --log-file FILE       Log to FILE
  --log-level {debug,info,error,critical}
                        Log level
```

# Example

This is an example of bi-directional communication between a TCP client and a TCP server through wstunnel. We'll use `.crt` to indicate the public key of certificates and `.pem` the private key.

## Server

Server is reachable via `1.2.3.4:5678` and upstream TCP server is listening on `127.0.0.1:2222` which will be accessible through wstunnel. Then a `routes.json` needs to be created on the server.

```
{
    "/tcp/server": {
        "Endpoint": "tcp://127.0.0.1:2222",
        "Token": "Very_secret_token"
    }
}
```

If TLS and client verification are to be enabled, the credentials, namely `server.pem` and `client.crt`, need to be transfered to the server beforehand.

Then start the server side of wstunnel.

```
python3 server.py -l 1.2.3.4:5678 -r routes.json -s server.pem -c client.crt
```

## Client

On the client side, create a `password_file`.

```
Very_secret_token
```

Also, make sure `server.crt` and `client.pem` are accessible. `server.crt` is optional if it is signed by one of the system's CAs.

Start the client side of wstunnel.

```
python3 client.py -l tcp://127.0.0.1:2222 --url wss://1.2.3.4:5678/tcp/server -p password_file -s server.crt -c client.pem
```

TCP server on the remote side is now accessible via `127.0.0.1:2222`.
