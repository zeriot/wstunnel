#!/bin/bash
# Generate a self signed certificate
umask 077
# Parameters
S="${1:-server}"
A="${2:-ecc}"

if [ "${A}" == "rsa" ]; then
    openssl genrsa -out "${S}.key" 4096;
else
    openssl ecparam -genkey -name prime256v1 -out "${S}.key"
fi
openssl req -new -sha256 -key "${S}.key" -out "${S}.csr"
openssl req -x509 -sha256 -days 730 -key "${S}.key" -in "${S}.csr" -out "${S}.crt"
cat "${S}.key" "${S}.crt" > "${S}.pem"
rm -f "${S}.csr" "${S}.key"
echo "Then copy ${S}.crt to the other side"
