## Installation
add .gitstack to your global gitignore
clone this repo
currently, I just add the path to my clone to my PATH

## Usage

### Create new branch
gst b new_branch_name (from trunk)
gst b new_branch_name parent_branch
gst b new_branch_name . (same as above)

### Go down stack
gst d

### Rebase all branches on trunk
gst s

### Track branch not tracked by gst
gst t parent_branch
