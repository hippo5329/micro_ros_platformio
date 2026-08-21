import os, sys
import yaml
import shutil

from .utils import run_cmd
from .repositories import Repository, Sources

class CMakeToolchain:
    def __init__(self, path, cc, cxx, ar, cflags, cxxflags):
        cmake_toolchain = """include(CMakeForceCompiler)
set(CMAKE_SYSTEM_NAME Generic)

set(CMAKE_CROSSCOMPILING 1)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

SET (CMAKE_C_COMPILER_WORKS 1)
SET (CMAKE_CXX_COMPILER_WORKS 1)

set(CMAKE_C_COMPILER {C_COMPILER})
set(CMAKE_CXX_COMPILER {CXX_COMPILER})
set(CMAKE_AR {AR_COMPILER})

set(CMAKE_C_FLAGS_INIT "{C_FLAGS}" CACHE STRING "" FORCE)
set(CMAKE_CXX_FLAGS_INIT "{CXX_FLAGS}" CACHE STRING "" FORCE)

set(__BIG_ENDIAN__ 0)"""

        cmake_toolchain = cmake_toolchain.format(C_COMPILER=cc, CXX_COMPILER=cxx, AR_COMPILER=ar, C_FLAGS=cflags, CXX_FLAGS=cxxflags)

        with open(path, "w") as file:
            file.write(cmake_toolchain)

        self.path = os.path.realpath(file.name)

class Build:
    def __init__(self, library_folder, packages_folder, distro, python_env):
        self.library_folder = library_folder
        self.packages_folder = packages_folder
        self.build_folder = library_folder + "/build"
        self.distro = distro

        self.dev_packages = []
        self.mcu_packages = []

        self.dev_folder = self.build_folder + '/dev'
        self.dev_src_folder = self.dev_folder + '/src'
        self.mcu_folder = self.build_folder + '/mcu'
        self.mcu_src_folder = self.mcu_folder + '/src'

        self.library_path = library_folder + '/libmicroros'
        self.library = self.library_path + "/libmicroros.a"
        self.includes = self.library_path+ '/include'
        self.library_name = "microros"
        self.python_env = python_env
        self.env = None

    def run(self, meta, toolchain, user_meta = ""):
        if os.path.exists(self.library):
            print("micro-ROS already built")
            return

        self.check_env()
        self.download_dev_environment()
        self.build_dev_environment()
        self.download_mcu_environment()
        self.build_mcu_environment(meta, toolchain, user_meta)
        self.package_mcu_library()

    def ignore_package(self, name):
        for p in self.mcu_packages:
            if p.name == name:
                p.ignore()

    def check_env(self):
        ROS_DISTRO = os.getenv('ROS_DISTRO')

        if (ROS_DISTRO):
            PATH = os.getenv('PATH')
            os.environ['PATH'] = PATH.replace('/opt/ros/{}/bin:'.format(ROS_DISTRO), '')
            os.environ.pop('AMENT_PREFIX_PATH', None)

        RMW_IMPLEMENTATION = os.getenv('RMW_IMPLEMENTATION')

        if (RMW_IMPLEMENTATION):
            os.environ['RMW_IMPLEMENTATION'] = "rmw_microxrcedds"

    def download_dev_environment(self):
        print("Downloading micro-ROS dev dependencies")
        self.dev_packages = Sources.dev_environments[self.distro]
        for p in self.dev_packages:
            p.clone(self.dev_src_folder)
            print("\t - Downloaded {}".format(p.name))

    def build_dev_environment(self):
        print("Building micro-ROS dev dependencies")
        self.patch_dev_sources()

        colcon_command = '. {} && colcon build --merge-install --packages-ignore-regex=.*_cpp --cmake-args -DPython3_EXECUTABLE=`which python` -DBUILD_TESTING=OFF'.format(self.python_env)
        command = "cd {} && {}".format(self.dev_folder, colcon_command)
        result = run_cmd(command)

        if 0 != result.returncode:
            print("Build dev micro-ROS environment failed: \n{}".format(result.stderr.decode("utf-8")))
            sys.exit(1)

    def download_mcu_environment(self):
        print("Downloading micro-ROS library")
        self.mcu_packages = Sources.mcu_environments[self.distro]
        for p in self.mcu_packages:
            p.clone(self.mcu_src_folder)
            if p.name in Sources.ignore_packages[self.distro]:
                p.ignore()
                print("\t - Downloaded {} (ignored)".format(p.name))
            else:
                print("\t - Downloaded {}".format(p.name))

        # Load and clone repositories from extra_packages.repos file
        if os.path.exists(self.packages_folder):
            extra_repos = self.get_repositories_from_yaml("{}/extra_packages.repos".format(self.packages_folder))
            for repo_name in extra_repos:
                repo_values = extra_repos[repo_name]
                version = repo_values['version'] if 'version' in repo_values else None
                Repository(repo_name, repo_values['url'], self.distro, version).clone(self.mcu_src_folder)
                print("\t - Downloaded {}".format(repo_name))

            extra_folders = os.listdir(self.packages_folder)
            if 'extra_packages.repos' in extra_folders:
                extra_folders.remove('extra_packages.repos')

            for folder in extra_folders:
                print("\t - Adding {}".format(folder))

            shutil.copytree(self.packages_folder, self.mcu_src_folder, ignore=shutil.ignore_patterns('extra_packages.repos'), dirs_exist_ok=True)

        # Apply COLCON_IGNORE for ignored packages
        for root, dirs, files in os.walk(self.mcu_src_folder):
            if os.path.basename(root) in Sources.ignore_packages.get(self.distro, []):
                with open(os.path.join(root, "COLCON_IGNORE"), "w") as f:
                    pass

    def get_repositories_from_yaml(self, yaml_file):
        repos = {}
        try:
            with open(yaml_file, 'r') as repos_file:
                root = yaml.safe_load(repos_file)
                repositories = root['repositories']

            if repositories:
                for path in repositories:
                    repo = {}
                    attributes = repositories[path]
                    try:
                        repo['type'] = attributes['type']
                        repo['url'] = attributes['url']
                        if 'version' in attributes:
                            repo['version'] = attributes['version']
                    except KeyError as e:
                        continue
                    repos[path] = repo
        except (yaml.YAMLError, KeyError, TypeError, FileNotFoundError) as e:
            pass
        return repos

    def build_mcu_environment(self, meta_file, toolchain_file, user_meta = ""):
        print("Building micro-ROS library")
        if self.distro in ("lyrical", "rolling"):
            self.patch_mcu_sources()

        common_meta_path = self.library_folder + '/metas/common.meta'
        colcon_command = '. {} && colcon build --merge-install --packages-ignore-regex=.*_cpp --metas {} {} {} --cmake-args -DCMAKE_POSITION_INDEPENDENT_CODE:BOOL=OFF  -DTHIRDPARTY=ON  -DBUILD_SHARED_LIBS=OFF  -DBUILD_TESTING=OFF  -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE={} -DPython3_EXECUTABLE=`which python`'.format(self.python_env, common_meta_path, meta_file, user_meta, toolchain_file)
        command = "cd {} && . {}/install/setup.sh && {}".format(self.mcu_folder, self.dev_folder, colcon_command)
        result = run_cmd(command, env=self.env)

        if 0 != result.returncode:
            print("Build mcu micro-ROS environment failed: \n{}".format(result.stderr.decode("utf-8")))
            sys.exit(1)

    def package_mcu_library(self):
        binutils_path = self.resolve_binutils_path()
        aux_folder = self.build_folder + "/aux"

        shutil.rmtree(aux_folder, ignore_errors=True)
        shutil.rmtree(self.library_path, ignore_errors=True)
        os.makedirs(aux_folder, exist_ok=True)
        os.makedirs(self.library_path, exist_ok=True)
        for root, dirs, files in os.walk(self.mcu_folder + "/install/lib"):
            for f in files:
                if f.endswith('.a'):
                    os.makedirs(aux_folder + "/naming", exist_ok=True)
                    os.chdir(aux_folder + "/naming")
                    os.system("{}ar x {}".format(binutils_path, root + "/" + f))
                    for obj in [x for x in os.listdir() if x.endswith('obj')]:
                        os.rename(obj, '../' + f.split('.')[0] + "__" + obj)

        os.chdir(aux_folder)
        command = "{binutils}ar rc libmicroros.a $(ls *.o *.obj 2> /dev/null); rm *.o *.obj 2> /dev/null; {binutils}ranlib libmicroros.a".format(binutils=binutils_path)
        result = run_cmd(command)

        if 0 != result.returncode:
            print("micro-ROS static library build failed: \n{}".format(result.stderr.decode("utf-8")))
            sys.exit(1)

        os.rename('libmicroros.a', self.library)

        # Copy includes
        shutil.copytree(self.build_folder + "/mcu/install/include", self.includes)

        if self.distro in ("lyrical", "rolling"):
            timer_h = os.path.join(self.includes, "rclc", "timer.h")
            if os.path.exists(timer_h):
                with open(timer_h, "r") as f_t:
                    ct = f_t.read()
                if "rclc_timer_init_default2" not in ct:
                    decl = """
/**
 *  Initializes a rcl timer with default clock.
 *
 *  \\param[inout] timer a preallocated rcl_timer_t
 *  \\param[in] support the rclc_support_t object
 *  \\param[in] timeout_ns the time out in nanoseconds of the timer
 *  \\param[in] callback the callback of the timer
 *  \\return `RCL_RET_OK` if the timer was successfully initialized
 *  \\return `RCL_RET_INVALID_ARGUMENT` if any null pointer as argument
 *  \\return `RCL_RET_ERROR` in case of other error
 */
RCLC_PUBLIC
rcl_ret_t
rclc_timer_init_default(
  rcl_timer_t * timer,
  rclc_support_t * support,
  const uint64_t timeout_ns,
  const rcl_timer_callback_t callback);
"""
                    decl_compat = """
#define rclc_timer_init_default(timer, support, timeout_ns, callback) \\
  rclc_timer_init_default2(timer, support, timeout_ns, (rcl_timer_callback_t)(callback), true)

#define rclc_timer_init_default2(timer, support, timeout_ns, callback, autostart) \\
  rcl_timer_init2(timer, &(support)->clock, &(support)->context, timeout_ns, (rcl_timer_callback_t)(callback), *(support)->allocator, autostart)
"""
                    ct = ct.replace(decl, decl_compat)
                    with open(timer_h, "w") as f_t:
                        f_t.write(ct)

        # Fix include paths for repeated nested folders
        include_folders = os.listdir(self.includes)
        for folder in include_folders:
            folder_path = self.includes + "/{}".format(folder)
            repeated_path = folder_path + "/{}".format(folder)
            if os.path.exists(repeated_path):
                shutil.copytree(repeated_path, folder_path, copy_function=shutil.move, dirs_exist_ok=True)
                shutil.rmtree(repeated_path)

    def resolve_binutils_path(self):
        if sys.platform == "darwin":
            homebrew_binutils_path = "/opt/homebrew/opt/binutils/bin/"
            if os.path.exists(homebrew_binutils_path):
                return homebrew_binutils_path
            print("ERROR: GNU binutils not found. ({}) Please install binutils with homebrew: brew install binutils"
                  .format(homebrew_binutils_path))
            sys.exit(1)

        path = os.getenv('PATH', '')
        for p in path.split(':'):
            if p.endswith('arm-none-eabi/bin'):
                return p + "/"
        return ""

    def patch_dev_sources(self):
        # 1. Ignore test packages in dev sources
        for p in ["rmw_test_fixture", "rmw_test_fixture_implementation", "domain_coordinator"]:
            p_dir = os.path.join(self.dev_src_folder, "ament_cmake_ros", p)
            if os.path.exists(p_dir):
                with open(os.path.join(p_dir, "COLCON_IGNORE"), "w") as f:
                    pass

        # 2. Relax C/C++ standards for embedded MCU cross-compilers (e.g. GCC 7.2.1 on Portenta)
        defaults_cmake = os.path.join(self.dev_src_folder, "ament_cmake_ros", "ament_cmake_ros_core", "cmake", "ament_ros_defaults.cmake")
        if os.path.exists(defaults_cmake):
            with open(defaults_cmake, "r") as f:
                dc = f.read()
            dc = dc.replace("cxx_std_20", "cxx_std_17").replace("c_std_17", "c_std_11")
            with open(defaults_cmake, "w") as f:
                f.write(dc)

        all_cmake = os.path.join(self.dev_src_folder, "ament_cmake", "ament_cmake_core", "cmake", "core", "all.cmake")
        if os.path.exists(all_cmake):
            with open(all_cmake, "r") as f:
                ac = f.read()
            if "macro(ament_target_dependencies" not in ac:
                macro_def = """

# Compatibility ament_target_dependencies macro for micro-ROS
macro(ament_target_dependencies target)
  cmake_parse_arguments(_ARG "SYSTEM;INTERFACE;PUBLIC;PRIVATE" "" "" ${ARGN})
  set(_dependencies ${_ARG_UNPARSED_ARGUMENTS})
  foreach(_dep ${_dependencies})
    find_package(${_dep} QUIET)
    if(TARGET ${_dep})
      if(_ARG_INTERFACE)
        target_link_libraries(${target} INTERFACE ${_dep})
      elseif(_ARG_PUBLIC)
        target_link_libraries(${target} PUBLIC ${_dep})
      else()
        target_link_libraries(${target} PRIVATE ${_dep})
      endif()
    elseif(TARGET ${_dep}::${_dep})
      if(_ARG_INTERFACE)
        target_link_libraries(${target} INTERFACE ${_dep}::${_dep})
      elseif(_ARG_PUBLIC)
        target_link_libraries(${target} PUBLIC ${_dep}::${_dep})
      else()
        target_link_libraries(${target} PRIVATE ${_dep}::${_dep})
      endif()
    endif()
    if(${_dep}_INCLUDE_DIRS)
      if(_ARG_INTERFACE)
        target_include_directories(${target} INTERFACE ${${_dep}_INCLUDE_DIRS})
      elseif(_ARG_PUBLIC)
        target_include_directories(${target} PUBLIC ${${_dep}_INCLUDE_DIRS})
      else()
        target_include_directories(${target} PRIVATE ${${_dep}_INCLUDE_DIRS})
      endif()
    endif()
    if(${_dep}_LIBRARIES)
      if(_ARG_INTERFACE)
        target_link_libraries(${target} INTERFACE ${${_dep}_LIBRARIES})
      elseif(_ARG_PUBLIC)
        target_link_libraries(${target} PUBLIC ${${_dep}_LIBRARIES})
      else()
        target_link_libraries(${target} PRIVATE ${${_dep}_LIBRARIES})
      endif()
    endif()
  endforeach()
endmacro()
"""
                with open(all_cmake, "a") as f:
                    f.write(macro_def)

    def patch_mcu_sources(self):
        # 1. Patch rcutils base64.c for no thread support
        base64_c = os.path.join(self.mcu_src_folder, "rcutils", "src", "base64.c")
        if os.path.exists(base64_c):
            with open(base64_c, "r") as f:
                bc = f.read()
            if "RCUTILS_NO_THREAD_SUPPORT" not in bc:
                target1 = "#ifdef _WIN32\nstatic INIT_ONCE base64_map_initialization_once = INIT_ONCE_STATIC_INIT;"
                rep1 = "#if defined(RCUTILS_NO_THREAD_SUPPORT)\nstatic bool base64_map_initialized = false;\n#elif defined(_WIN32)\nstatic INIT_ONCE base64_map_initialization_once = INIT_ONCE_STATIC_INIT;"
                target2 = "#else\n  pthread_once(&base64_map_initialization_once, initialize_base64_map);\n#endif"
                rep2 = "#elif defined(RCUTILS_NO_THREAD_SUPPORT)\n  if (!base64_map_initialized) { initialize_base64_map(); base64_map_initialized = true; }\n#else\n  pthread_once(&base64_map_initialization_once, initialize_base64_map);\n#endif"
                bc = bc.replace(target1, rep1).replace(target2, rep2)
                with open(base64_c, "w") as f:
                    f.write(bc)

        # 2. Patch rosidl_buffer CMakeLists.txt and buffer.hpp
        for b_dir in ["rosidl", "rosidl_core"]:
            buffer_cmake = os.path.join(self.mcu_src_folder, b_dir, "rosidl_buffer", "CMakeLists.txt")
            if os.path.exists(buffer_cmake):
                with open(buffer_cmake, "r") as f:
                    bc = f.read()
                bc_lines = []
                changed_cmake = False
                for line in bc.splitlines(True):
                    if "target_link_libraries" in line and ("ament_cmake_ros_core::ament_ros_cxx_standard" in line or "ament_ros_cxx_standard" in line or "PRIVATE )" in line or "PRIVATE  )" in line):
                        bc_lines.append("target_compile_features(${PROJECT_NAME} PUBLIC cxx_std_17)\n")
                        changed_cmake = True
                    else:
                        bc_lines.append(line)
                if changed_cmake:
                    with open(buffer_cmake, "w") as f:
                        f.write("".join(bc_lines))

            buffer_hpp = os.path.join(self.mcu_src_folder, b_dir, "rosidl_buffer", "include", "rosidl_buffer", "buffer.hpp")
            if os.path.exists(buffer_hpp):
                with open(buffer_hpp, "r") as f:
                    bh = f.read()
                modified_bh = False
                if "std::is_same_v" in bh:
                    bh = bh.replace("if constexpr (std::is_same_v<Allocator, std::allocator<T>>)", "if (std::is_same<Allocator, std::allocator<T>>::value)")
                    bh = bh.replace("std::is_same_v<Allocator, std::allocator<T>>", "std::is_same<Allocator, std::allocator<T>>::value")
                    modified_bh = True
                if "__EXCEPTIONS" not in bh:
                    orig_throw1 = '      throw std::invalid_argument("Buffer implementation must not be null");'
                    patch_throw1 = '#if __EXCEPTIONS || defined(__cpp_exceptions)\n      throw std::invalid_argument("Buffer implementation must not be null");\n#endif'
                    bh = bh.replace(orig_throw1, patch_throw1)

                    orig_fn = """  void throw_if_not_cpu_backend() const
  {
    if (!cpu_impl_) {
      throw std::runtime_error(
              "Operation requires CPU backend. Current backend: " +
              impl_->get_backend_type() +
              ". Use to_vector() for explicit conversion to CPU.");
    }
  }"""
                    patch_fn = """  void throw_if_not_cpu_backend() const
  {
#if __EXCEPTIONS || defined(__cpp_exceptions)
    if (!cpu_impl_) {
      throw std::runtime_error(
              "Operation requires CPU backend. Current backend: " +
              impl_->get_backend_type() +
              ". Use to_vector() for explicit conversion to CPU.");
    }
#endif
  }"""
                    bh = bh.replace(orig_fn, patch_fn)
                    modified_bh = True
                if modified_bh:
                    with open(buffer_hpp, "w") as f:
                        f.write(bh)

        # 3. Patch rclc timer.c for rcl_timer_init2
        timer_c = os.path.join(self.mcu_src_folder, "rclc", "rclc", "src", "rclc", "timer.c")
        if os.path.exists(timer_c):
            with open(timer_c, "r") as f:
                tc = f.read()
            if "rcl_timer_init2" not in tc:
                target_timer = """  rcl_ret_t rc = rcl_timer_init(
    timer,
    &support->clock,
    &support->context,
    timeout_ns,
    callback,
    (*support->allocator));"""
                rep_timer = """  rcl_ret_t rc = rcl_timer_init2(
    timer,
    &support->clock,
    &support->context,
    timeout_ns,
    callback,
    (*support->allocator),
    true);"""
                tc = tc.replace(target_timer, rep_timer)
                with open(timer_c, "w") as f:
                    f.write(tc)

        # 4. Patch rclc_lifecycle for rcl_lifecycle_state_machine_init clock parameter
        lc_c = os.path.join(self.mcu_src_folder, "rclc", "rclc_lifecycle", "src", "rclc_lifecycle", "rclc_lifecycle.c")
        if os.path.exists(lc_c):
            with open(lc_c, "r") as f:
                lcc = f.read()
            if "lifecycle_clock" not in lcc:
                target_lc = """  rcl_ret_t rcl_ret = rcl_lifecycle_state_machine_init(
    state_machine,
    node,
    ROSIDL_GET_MSG_TYPE_SUPPORT(lifecycle_msgs, msg, TransitionEvent),"""
                rep_lc = """  static rcl_clock_t lifecycle_clock;
  static bool lifecycle_clock_initialized = false;
  if (!lifecycle_clock_initialized) {
    rcl_ros_clock_init(&lifecycle_clock, allocator);
    lifecycle_clock_initialized = true;
  }

  rcl_ret_t rcl_ret = rcl_lifecycle_state_machine_init(
    state_machine,
    node,
    &lifecycle_clock,
    ROSIDL_GET_MSG_TYPE_SUPPORT(lifecycle_msgs, msg, TransitionEvent),"""
                lcc = "#include <rcl/time.h>\n" + lcc.replace(target_lc, rep_lc)
                with open(lc_c, "w") as f:
                    f.write(lcc)
