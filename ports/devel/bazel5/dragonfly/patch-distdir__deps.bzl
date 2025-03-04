--- distdir_deps.bzl.intermediate	Tue Mar  4 00:01:15 2025
+++ distdir_deps.bzl	Tue Mar
@@ -21,10 +21,9 @@ DIST_DEPS = {
     ########################################
     "platforms": {
         "archive": "platforms-0.0.5.tar.gz",
-        "sha256": "379113459b0feaf6bfbb584a91874c065078aa673222846ac765f86661c27407",
+        "sha256": "9d4a2c08a78841105fbd8c59f7149dd97bae92aa85da33885472cc62880f95a4",
         "urls": [
-            "https://mirror.bazel.build/github.com/bazelbuild/platforms/releases/download/0.0.5/platforms-0.0.5.tar.gz",
-            "https://github.com/bazelbuild/platforms/releases/download/0.0.5/platforms-0.0.5.tar.gz",
+	    "https://avalon.dragonflybsd.org/misc/distfiles/platforms-0.0.5-dfly.tar.gz"
         ],
         "used_in": [
             "additional_distfiles",
