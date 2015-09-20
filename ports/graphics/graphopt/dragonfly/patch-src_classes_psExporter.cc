--- src/classes/psExporter.cc.bak	2015-09-20 16:47:32.000000000 +0300
+++ src/classes/psExporter.cc
@@ -1,5 +1,6 @@
 #include "psExporter.h"
 #include <cstdlib>
+#include <cstring>
 
 
 psExporter::psExporter(char *what_file, nodes *what_nodes) {
