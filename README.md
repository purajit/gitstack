## Installation
add .gitstack to your global gitignore
clone this repo
currently, I just add the path to my clone to my PATH

## Usage

### Create new branch
```sh
gst b new_branch_name  # (from trunk)
gst b new_branch_name parent_branch
gst b new_branch_name .  # (same as above)
```

### Go down stack
```sh
gst d
```
### Rebase all branches on trunk
```sh
gst s
```
### Track branch not tracked by gst
```sh
gst t parent_branch
```
