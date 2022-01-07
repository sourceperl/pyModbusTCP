## How to set package developer mode (also call editable mode on pip)

*After set this, we can directly test effect of editing a package files
without need to fully reinstall it.*

Turn on develop mode (add current package files to python path):

```bash
sudo python3 setup.py develop
```
Turn off:

```bash
sudo python3 setup.py develop --uninstall
```
View the current python path:

```bash
python3 -c 'import sys; print(sys.path)'
```
