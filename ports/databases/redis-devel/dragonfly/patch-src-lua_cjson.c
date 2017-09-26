--- redis-devel/redis-4.0.1/src/lua_cjson.c	2017-09-22 20:32:36.607128000 +0300
+++ redis-devel.new/redis-4.0.1/src/lua_cjson.c	2017-09-22 20:25:42.967004000 +0300
@@ -46,7 +46,7 @@
 #include "strbuf.h"
 #include "fpconv.h"
 
-#include "../../../src/solarisfixes.h"
+#include "solarisfixes.h"
 
 #ifndef CJSON_MODNAME
 #define CJSON_MODNAME   "cjson"
