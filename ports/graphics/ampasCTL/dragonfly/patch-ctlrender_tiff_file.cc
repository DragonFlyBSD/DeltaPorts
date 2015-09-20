--- ctlrender/tiff_file.cc.bak	2015-09-20 17:42:49.000000000 +0300
+++ ctlrender/tiff_file.cc
@@ -53,6 +53,7 @@
 ///////////////////////////////////////////////////////////////////////////
 
 #include "tiff_file.hh"
+#include <cstdlib>
 #include <stdarg.h>
 #include <dpx.hh>
 #if defined(HAVE_LIBTIFF)
