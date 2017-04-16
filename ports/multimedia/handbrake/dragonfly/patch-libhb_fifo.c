--- libhb/fifo.c.intermediate	2017-04-16 17:04:56 UTC
+++ libhb/fifo.c
@@ -13,10 +13,6 @@
 #include "qsv_libav.h"
 #endif
 
-#if !defined(SYS_DARWIN) && !defined(SYS_FREEBSD)
-#include <malloc.h>
-#endif
-
 #define FIFO_TIMEOUT 200
 //#define HB_FIFO_DEBUG 1
 // defining HB_BUFFER_DEBUG and HB_NO_BUFFER_POOL allows tracking
