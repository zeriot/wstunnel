#!/bin/bash
# Generate a self signed certificate
umask 277
echo "This script should be run on the client"
C="${1:-client}"
openssl ecparam -genkey -name prime256v1 -out "${C}.key"
openssl req -new -sha256 -key "${C}.key" -out "${C}.csr"
openssl req -x509 -sha256 -days 730 -key "${C}.key" -in "${C}.csr" -out "${C}.crt"
rm -f "${C}.csr"
cat "${C}.key" "${C}.crt" > "${C}.pem"
echo "Then copy ${C}.crt to the server"