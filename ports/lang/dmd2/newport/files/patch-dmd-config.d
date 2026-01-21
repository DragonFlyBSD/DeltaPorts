--- dmd/config.d	2019-06-26 11:20:31.001091000 +0200
+++ dmd/config.d	2019-06-26 11:21:55.463494000 +0200
@@ -48,10 +48,13 @@
 */
 string generateVersion(const string versionFile)
 {
-    import std.process : execute;
+    import std.process : execute, executeShell;
     import std.file : readText;
     import std.path : dirName;
     import std.string : strip;
+
+    if (executeShell("which git").status != 0)
+        return versionFile.readText;
 
     enum workDir = __FILE_FULL_PATH__.dirName;
     const result = execute(["git", "-C", workDir, "describe", "--dirty"]);
