--- src/ssl.c.orig	2017-03-26 20:25:00 UTC
+++ src/ssl.c
@@ -51,7 +51,7 @@ int index_ssl_cli, index_ssl_ctx_opt;
 int index_session_authenticated, index_session_connect_address;
 
 int ssl_init(void) { /* init TLS before parsing configuration file */
-#if OPENSSL_VERSION_NUMBER>=0x10100000L
+#if OPENSSL_VERSION_NUMBER>=0x10100000L && !defined(LIBRESSL_VERSION_NUMBER)
     OPENSSL_init_ssl(OPENSSL_INIT_LOAD_SSL_STRINGS |
         OPENSSL_INIT_LOAD_CRYPTO_STRINGS, NULL);
 #else
@@ -86,7 +86,7 @@ int ssl_init(void) { /* init TLS before
 }
 
 #ifndef OPENSSL_NO_DH
-#if OPENSSL_VERSION_NUMBER<0x10100000L
+#if OPENSSL_VERSION_NUMBER<0x10100000L || defined(LIBRESSL_VERSION_NUMBER)
 /* this is needed for dhparam.c generated with OpenSSL >= 1.1.0
  * to be linked against the older versions */
 int DH_set0_pqg(DH *dh, BIGNUM *p, BIGNUM *q, BIGNUM *g) {
