--- src/PYLibPinyin.cc.bak	2016-06-15 14:44:39.000000000 +0300
+++ src/PYLibPinyin.cc
@@ -219,7 +219,7 @@ LibPinyinBackEnd::importPinyinDictionary
         return FALSE;
 
     char* linebuf = NULL; size_t size = 0; ssize_t read;
-    while ((read = getline (&linebuf, &size, dictfile)) != -1) {
+    while ((read = std::getline (&linebuf, &size, dictfile)) != -1) {
         if (0 == strlen (linebuf))
             continue;
 
