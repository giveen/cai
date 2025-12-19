"""
SpiderFoot reconnaissance tool
"""
from cai.tools.common import run_command   # pylint: disable=import-error
from cai.sdk.agents import function_tool

@function_tool
def spiderfoot(
    target: str,
    modules: str = '',
    event_types: str = '',
    use_case: str = 'all',
    output_format: str = 'tab',
    debug: bool = False,
    strict_mode: bool = False
) -> str:
    """
    SpiderFoot reconnaissance tool for gathering open source intelligence.
    
    Args:
        target: Target for the scan
        modules: Modules to enable (comma-separated)
        event_types: Event types to collect (comma-separated)
        use_case: Select modules automatically by use case (all, footprint, investigate, passive)
        output_format: Output format (tab, csv, json)
        debug: Enable debug output
        strict_mode: Enable strict mode
        
    Returns:
        str: The output of running the spiderfoot command
    """
    command = ['spiderfoot']
    
    if debug:
        command.append('-d')
        
    if target:
        command.extend(['-s', target])
        
    if modules:
        command.extend(['-m', modules])
        
    if event_types:
        command.extend(['-t', event_types])
        
    if use_case != 'all':
        command.extend(['-u', use_case])
        
    if output_format:
        command.extend(['-o', output_format])
        
    if strict_mode:
        command.append('-x')
    
    # Join the command list into a string
    cmd_string = ' '.join(command)
    return run_command(cmd_string)
