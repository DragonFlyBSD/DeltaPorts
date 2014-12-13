--- cmd-line-utils/libedit/chartype.h.orig	2014-10-29 08:24:20 UTC
+++ cmd-line-utils/libedit/chartype.h
@@ -49,6 +49,7 @@
   TODO : Verify if FreeBSD & AIX stores ISO 10646 in wchar_t. */
 #if !defined(__NetBSD__) && !defined(__sun) \
   && !(defined(__APPLE__) && defined(__MACH__)) \
+  && !defined(__DragonFly__) \
   && !defined(__FreeBSD__) && !defined(_AIX)
 #ifndef __STDC_ISO_10646__
 /* In many places it is assumed that the first 127 code points are ASCII
