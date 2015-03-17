--- src/las_c_api.cpp.orig	2009-10-02 17:01:59 UTC
+++ src/las_c_api.cpp
@@ -745,7 +745,7 @@ LAS_DLL LASErrorEnum LASHeader_SetProjec
 
     try {
         liblas::guid id;
-        id = liblas::guid::guid(value);
+        id = liblas::guid(value);
         ((LASHeader*) hHeader)->SetProjectId(id);    
     } catch (std::exception const& e)
     {
