--- druntime/src/core/sys/dragonflybsd/dlfcn.d	2019-11-19 00:00:19.501296000 -0800
+++ druntime/src/core/sys/dragonflybsd/dlfcn.d	2019-11-19 00:01:29.490870000 -0800
@@ -90,8 +90,10 @@
 /* XSI functions first. */
 static assert(is(typeof(&dlclose) == __externC!(int, void*)));
 static assert(is(typeof(&dlerror) == __externC!(char*)));
-static assert(is(typeof(&dlopen)  == __externC!(void*, const char*, int)));
-static assert(is(typeof(&dlsym)   == __externC!(void*, void*, const char*)));
+//static assert(is(typeof(&dlopen)  == __externC!(void*, const char*, int)));
+static assert(is(typeof(&dlopen)  == void* function(scope const(char*), int) nothrow @nogc ));
+//static assert(is(typeof(&dlsym)   == __externC!(void*, void*, const char*)));
+static assert(is(typeof(&dlsym)   == void* function(void *, scope const(char*)) nothrow @nogc));
 
 void*    fdlopen(int, int);
 int      dladdr(const(void)*, Dl_info*);
