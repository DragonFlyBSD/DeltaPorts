--- src/ptex.imageio/ptex/PtexPlatform.h.orig	2014-11-25 07:10:44.000000000 +0200
+++ src/ptex.imageio/ptex/PtexPlatform.h
@@ -60,7 +60,7 @@ OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 
 // linux/unix/posix
 #include <stdlib.h>
-#if !defined(__FreeBSD__) && !defined(__OpenBSD__)
+#if !defined(__FreeBSD__) && !defined(__OpenBSD__) && !defined(__DragonFly__)
 #include <alloca.h>
 #endif
 #include <string.h>
