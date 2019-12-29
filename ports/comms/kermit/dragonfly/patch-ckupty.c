--- ckupty.c.orig	2017-04-26 15:57:10 UTC
+++ ckupty.c
@@ -308,6 +308,10 @@ char * ptyver = "PTY support 8.0.016, 22
 #include <tty.h>
 #endif /* HAVE_TTY_H */
 
+#ifdef __DragonFly__
+#include <libutil.h>  /* for openpty() */
+#endif
+
 /*
   Because of the way ptyibuf is used with streams messages, we need
   ptyibuf+1 to be on a full-word boundary.  The following weirdness
