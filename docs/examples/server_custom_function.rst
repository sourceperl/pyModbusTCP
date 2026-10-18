Server: custom functions
========================

Add a function 0x41, replace the standard function 0x06 to check a setpoint range, and log the unsupported functions with a fallback handler.

.. literalinclude:: ../../examples/server_custom_function.py
   :caption: examples/server_custom_function.py
   :language: python
