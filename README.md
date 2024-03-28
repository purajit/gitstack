## Installation
add .gitstack to your global gitignore
clone this repo
currently, I just add the path to my clone to my PATH

## Usage

### Create new branch
```sh
gst b new_branch_name  # from trunk
gst b new_branch_name parent_branch
gst b new_branch_name .  # from current branch
```

### Print the stack
```sh
gst p
```

### Traverse the stack
```sh
gst d  # down
gst u  # up, will ask you to choose if multiple options
```
### Rebase all branches on trunk
```sh
gst s
```
### Track branch not tracked by gst
```sh
gst t parent_branch
```
