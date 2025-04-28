#!/usr/bin/env python3
import argparse
import re
import sys

# The prefix to remove from .env variable names
PREFIX = "CONDUWUIT_"

def parse_env_file(env_path):
    """
    Parse the .env file into a dictionary.
    It removes the defined prefix and lowercases the variable names.
    """
    env_vars = {}
    try:
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.match(r'^([^=]+)=(.*)$', line)
                if m:
                    key, value = m.group(1).strip(), m.group(2).strip()
                    if key.startswith(PREFIX):
                        key = key[len(PREFIX):]
                    key = key.lower()
                    if (value.startswith('"') and value.endswith('"')) or (
                        value.startswith("'") and value.endswith("'")
                    ):
                        value = value[1:-1]
                    env_vars[key] = value
    except Exception as e:
        sys.exit(f"Error reading {env_path}: {e}")
    return env_vars

def format_value(val):
    """
    Format the value appropriately.
    If the value is a valid boolean or numeric value, return in that form.
    For lists (detected by '[' at the start and ']' at the end), return as is.
    Otherwise return a quoted string.
    """
    # Boolean check.
    if val.lower() in {"true", "false"}:
        return val.lower()
    try:
        int_val = int(val)
        return str(int_val)
    except ValueError:
        pass
    try:
        float_val = float(val)
        return str(float_val)
    except ValueError:
        pass
    if val.startswith("[") and val.endswith("]"):
        return val
    return f"\"{val}\""

def process_toml_file(toml_path, env_vars):
    """
    Process a commented-out TOML file by reading it line by line.
    Lines that start with '#' immediately followed by an alphabetical character are
    considered potential configuration keys.
    
    The uncommenting and value substitution occurs ONLY if:
     - The key (after lowercasing) exists in env_vars, OR
     - The original value is empty (meaning it's mandatory and should be set to "REPLACE_ME").
    
    Otherwise, the line is left unchanged.
    """
    output_lines = []
    # Regex for lines starting with '#' then an alphabetical character,
    # capturing the key and any value after '='.
    pattern = re.compile(r'^#([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
    
    try:
        with open(toml_path, "r") as f:
            for line in f:
                match = pattern.match(line)
                if match:
                    key = match.group(1).strip()
                    orig_value = match.group(2).strip()
                    key_lower = key.lower()
                    
                    # Only update and uncomment if the env_vars has a matching key
                    # OR if the original value is empty (meaning we need to set a value).
                    if key_lower in env_vars or (orig_value == ""):
                        if key_lower in env_vars:
                            new_value = env_vars[key_lower]
                        elif orig_value == "":
                            new_value = "REPLACE_ME"
                        formatted = format_value(new_value)
                        # Write the new, uncommented line.
                        new_line = f"{key} = {formatted}\n"
                        output_lines.append(new_line)
                    else:
                        # Leave the line commented.
                        output_lines.append(line)
                else:
                    output_lines.append(line)
    except Exception as e:
        sys.exit(f"Error processing {toml_path}: {e}")
    return output_lines

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Update a commented-out .toml configuration file using values from a .env file. "
            "A line in the .toml starting with '#' immediately followed by an alphabetical character "
            "is treated as a potential configuration key. Such a line is uncommented and updated only "
            "if either a matching variable is found in the .env file (after removing the CONDUWUIT_ prefix "
            "and lowercasing) or if the originally commented line has an empty value (in which case it is "
            "filled with 'REPLACE_ME')."
        )
    )
    parser.add_argument("--env", required=True, help="Path to the .env file")
    parser.add_argument("--toml", required=True, help="Path to the commented .toml configuration file")
    parser.add_argument("--output", required=False, help="Path to write the updated configuration. If not provided, the original file will be overwritten.")
    
    args = parser.parse_args()
    
    env_vars = parse_env_file(args.env)
    updated_lines = process_toml_file(args.toml, env_vars)
    
    output_path = args.output if args.output else args.toml
    try:
        with open(output_path, "w") as f:
            f.writelines(updated_lines)
        print(f"Configuration updated and written to {output_path}")
    except Exception as e:
        sys.exit(f"Error writing to {output_path}: {e}")

if __name__ == "__main__":
    main()

