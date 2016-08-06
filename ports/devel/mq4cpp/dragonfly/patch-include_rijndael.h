--- include/rijndael.h.intermediate	2016-08-06 18:32:53 UTC
+++ include/rijndael.h
@@ -26,7 +26,7 @@ typedef unsigned char byte;
 #include <stdlib.h>
 #include <string.h>
 
-#ifdef __FreeBSD__
+#if defined(__FreeBSD__) || defined(__DragonFly__)
 #include <sys/types.h>
 typedef uint32_t word32;
 typedef int32_t sword32;
