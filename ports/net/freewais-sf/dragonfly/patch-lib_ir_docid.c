--- lib/ir/docid.c.orig	2000-06-08 06:33:07.000000000 +0000
+++ lib/ir/docid.c
@@ -22,6 +22,7 @@
 static char *PRCSid = "$Id: docid.c 1.5.1.8.1.3 Mon, 05 May 1997 11:54:27 +0200 pfeifer $";
 #endif
 
+#include <limits.h>
 #include "docid.h"
 #include "irfileio.h"
 #include "cutil.h"
@@ -153,14 +154,14 @@ char *str1;
 any* Any;
 FILE* file;
 {
-char outstr[MAXNAMLEN], str2[MAXNAMLEN];
+char outstr[NAME_MAX], str2[NAME_MAX];
 int i;
 
 #ifdef ASCII_ID
   for(i=0; i<=Any->size; i++) {
     str2[i] = Any->bytes[i];
     }
-  if (i<MAXNAMLEN) {
+  if (i<NAME_MAX) {
     str2[i] = '\0';
   } else {
     str2[i-1] = '\0';
