--- phobos/std/file.d	2019-06-30 13:05:22.802439000 -0700
+++ phobos/std/file.d	2019-06-30 13:06:49.058711000 -0700
@@ -3437,7 +3437,7 @@
 else version (FreeBSD)
     private extern (C) int sysctl (const int* name, uint namelen, void* oldp,
         size_t* oldlenp, const void* newp, size_t newlen);
-else version (NetBSD)
+else version (DragonFlyBSD)
     private extern (C) int sysctl (const int* name, uint namelen, void* oldp,
         size_t* oldlenp, const void* newp, size_t newlen);
 
@@ -3524,7 +3524,25 @@
     }
     else version (DragonFlyBSD)
     {
-        return readLink("/proc/curproc/file");
+        import std.exception : errnoEnforce, assumeUnique;
+        enum
+        {
+            CTL_KERN = 1,
+            KERN_PROC = 14,
+            KERN_PROC_PATHNAME = 9
+        }
+
+        int[4] mib = [CTL_KERN, KERN_PROC, KERN_PROC_PATHNAME, -1];
+        size_t len;
+
+        auto result = sysctl(mib.ptr, mib.length, null, &len, null, 0); // get the length of the path
+        errnoEnforce(result == 0);
+
+        auto buffer = new char[len - 1];
+        result = sysctl(mib.ptr, mib.length, buffer.ptr, &len, null, 0);
+        errnoEnforce(result == 0);
+
+        return buffer.assumeUnique;
     }
     else version (Solaris)
     {
