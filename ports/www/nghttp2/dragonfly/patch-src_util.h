--- src/util.h.orig	2015-01-06 16:44:23 UTC
+++ src/util.h
@@ -41,6 +41,10 @@
 
 #include "http-parser/http_parser.h"
 
+#ifndef SOCK_CLOEXEC
+#define SOCK_CLOEXEC 0
+#endif
+
 namespace nghttp2 {
 
 // The additional HTTP/2 protocol ALPN ID we also supports for our
