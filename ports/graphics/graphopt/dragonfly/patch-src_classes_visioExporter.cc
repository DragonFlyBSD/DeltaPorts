--- src/classes/visioExporter.cc.bak	2015-09-20 16:47:32.000000000 +0300
+++ src/classes/visioExporter.cc
@@ -1,5 +1,6 @@
 #include "visioExporter.h"
 #include <cstdlib>
+#include <cstring>
 
 
 visioExporter::visioExporter(char *what_file, nodes *what_nodes) {
