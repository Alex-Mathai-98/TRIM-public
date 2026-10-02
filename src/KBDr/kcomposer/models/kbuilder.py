# kbuilder.py

arch_map = {
    'amd64': 'amd64',
    'i386': '386',
    'arm64': 'arm64',
    'arm': 'arm'
}

def kbuilder_from_scratch_argument(
    kernel_git_url: str,
    commit_id: str,
    kernel_config: str,
    userspace_img_name: str,
    arch: str,
    compiler: str,
    linker: str,
    patch: str='') -> dict:
    """
    Compose a kbuilder (from scratch) argument

    Parameters:
    - kernel_git_url (str): The Git URL of the kernel repository,
    e.g https://git.kernel.org/pub/scm/linux/kernel/git/linux.git
    - commit_id (str): The commit ID
    - kernel_config (str): The `.config` file content
    - userspace_img_name (str): The userspace image filename in the bucket
    - arch (str): amd64, i386, etc
    - compiler (str): gcc, clang
    - linker (str): ld, ld.lld
    - patch (str | None): If fed with patch string, kbuilder will do 'git apply' with it
    
    Retunrs:
    - dict: kbuilder argument
    """
    ret_arg = {
        'kernel-git-url': kernel_git_url,
        'kernel-commit-id': commit_id,
        'kernel-config': kernel_config,
        'userspace-image-name': userspace_img_name,
        'kernel-arch': arch_map[arch],
        'compiler': compiler,
        'linker': linker
    }
    if patch != '':
        ret_arg['patch'] = patch
    return ret_arg

def kbuilder_from_kcache_argument(
    kcache_url: str,
    userspace_img_name: str,
    patch: str=''
) -> dict:
    """
    Compose a kbuilder (from kcache) argument

    Parameters:
    - kcache_url (str) : A url to the kcache for a pre-built kernel
    - userspace_img_name (str): The userspace image filename in the bucket
    - patch (str | None): If fed with patch string, kbuilder will do 'git apply' with it
    
    Retunrs:
    - dict: kbuilder argument
    """
    ret_arg = {
        'kcache-url': kcache_url,
        'userspace-image-name': userspace_img_name
    }
    if patch != '':
        ret_arg['patch'] = patch
    return ret_arg

def kbuilder_argument_from_bug(
    userspace_img_name: str,
    crash: dict,
    compiler: str,
    linker: str,
    patch: str=''):
    """
    Compose a kbuilder argument from a bug dict

    Parameters:
    - userspace_img_name (str): The userspace image filename in the bucket
    - crash (dict): The specific crash entry from the bug dict
    - compiler (str): gcc, clang
    - linker (str): ld, ld.lld

    Retunrs:
    - dict: kbuilder argument
    """
    if not (
        'kernel-config-data' in crash and
        'kernel-source-git' in crash and
        'kernel-source-commit' in crash and
        'architecture' in crash and
        crash['architecture'] in ('i386', 'amd64')
        ):
        raise ValueError('Insufficient parameters')
    # process git URL;
    git_url = crash['kernel-source-git']
    if 'https://github.com/' in git_url:
        git_url = git_url.split('/commits/')[0]
    elif 'https://git.kernel.org/pub/scm/linux/kernel/git/' in git_url:
        git_url = git_url.split('/log/?id=')[0]
    else:
        raise ValueError('Git URL not supported')
    if compiler == '':
        if 'clang' in crash.get('compiler-description', 'gcc'):
            compiler = 'clang'
        else:
            compiler = 'gcc'
    if linker == '':
        if compiler == 'clang':
            linker = 'ld.lld'
        else:
            linker = 'ld'
    # return the argument;
    return kbuilder_from_scratch_argument(
        git_url,
        crash['kernel-source-commit'],
        crash['kernel-config-data'],
        userspace_img_name,
        crash['architecture'],
        compiler,
        linker,
        patch
    )
