--- src/classes/optFileWriter.cc.bak	2015-09-20 16:43:59.000000000 +0300
+++ src/classes/optFileWriter.cc
@@ -1,5 +1,6 @@
 #include "optFileWriter.h"
 #include <cstdlib>
+#include <cstring>
 
 
 optFileWriter::optFileWriter(char *what_file, nodes *what_nodes) {
