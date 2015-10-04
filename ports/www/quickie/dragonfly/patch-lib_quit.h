--- lib/quit.h.orig	2006-05-21 01:56:52.000000000 +0300
+++ lib/quit.h
@@ -25,6 +25,9 @@
 #pragma interface "quit"
 
 #include <cstdarg>
+#ifdef __DragonFly__
+#include <cstring>
+#endif
 #include <format_printf.h>
 #include <quit/atexit_list.h>
 
