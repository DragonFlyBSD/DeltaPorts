--- runtime/druntime/src/core/sys/dragonflybsd/dlfcn.d.orig	2019-10-15 19:07:36 UTC
+++ runtime/druntime/src/core/sys/dragonflybsd/dlfcn.d
@@ -90,8 +90,10 @@ private template __externC(RT, P...)
 /* XSI functions first. */
 static assert(is(typeof(&dlclose) == __externC!(int, void*)));
 static assert(is(typeof(&dlerror) == __externC!(char*)));
-static assert(is(typeof(&dlopen)  == __externC!(void*, const char*, int)));
-static assert(is(typeof(&dlsym)   == __externC!(void*, void*, const char*)));
+//static assert(is(typeof(&dlopen)  == __externC!(void*, const char*, int)));
+static assert(is(typeof(&dlopen) == void* function(scope const(char*), int) nothrow @nogc));
+//static assert(is(typeof(&dlsym)   == __externC!(void*, void*, const char*)));
+static assert(is(typeof(&dlsym)   == void* function(void*, scope const(char*)) nothrow @nogc));
 
 void*    fdlopen(int, int);
 int      dladdr(const(void)*, Dl_info*);
