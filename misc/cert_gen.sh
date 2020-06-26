#!/bin/bash
# Generate a self signed certificate
umask 277
echo "This script should be run on the server"
S="${1:-server}"
openssl ecparam -genkey -name prime256v1 -out "${S}.key"
openssl req -new -sha256 -key "${S}.key" -out "${S}.csr"
openssl req -x509 -sha256 -days 730 -key "${S}.key" -in "${S}.csr" -out "${S}.crt"
rm -f "${S}.csr"
cat "${S}.key" "${S}.crt" > "${S}.pem"
echo "Then copy ${S}.crt to the client"