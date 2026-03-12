# Contributing to Alkaline Network

Thanks for wanting to help. Here's how.

---

## Ways To Contribute

### 1. Run A Gateway

The most valuable contribution. Share your internet with the network.

### 2. Host A Relay

No internet required. Just forward traffic for others.

### 3. Code

Areas that need work:

| Area | Difficulty | Impact |
|------|------------|--------|
| Encryption implementation | Hard | Critical |
| Mesh routing optimization | Medium | High |
| Mobile apps | Medium | High |
| Documentation | Easy | Medium |
| Testing | Easy | High |

### 4. Test

- Test on different hardware
- Report bugs
- Test in different environments

### 5. Documentation

- Fix typos
- Add examples
- Translate to other languages

---

## Setting Up Development Environment

```bash
# Clone
git clone https://github.com/AlkalineTech/alkaline-network.git
cd alkaline-network

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/
```

---

## Code Style

- Python: Follow PEP 8
- Use type hints
- Write docstrings
- Keep functions small
- Comment non-obvious code

### Example

```python
def compress_data(data: bytes, level: int = 9) -> bytes:
    """
    Compress data using zlib.
    
    Args:
        data: Raw bytes to compress
        level: Compression level 1-9 (default: 9)
        
    Returns:
        Compressed bytes
        
    Raises:
        ValueError: If level is not 1-9
    """
    if not 1 <= level <= 9:
        raise ValueError(f"Level must be 1-9, got {level}")
    
    return zlib.compress(data, level)
```

---

## Pull Request Process

1. Fork the repo
2. Create a branch: `git checkout -b my-feature`
3. Make changes
4. Run tests: `pytest tests/`
5. Commit: `git commit -m "Add my feature"`
6. Push: `git push origin my-feature`
7. Open a Pull Request

### PR Checklist

- [ ] Tests pass
- [ ] New tests for new features
- [ ] Documentation updated
- [ ] No breaking changes (or documented)
- [ ] Code follows style guide

---

## Reporting Bugs

Open an issue with:

1. What you expected
2. What happened
3. Steps to reproduce
4. Hardware/OS info
5. Logs if available

---

## Security Issues

**DO NOT** open public issues for security vulnerabilities.

Email: security@example.com

We'll respond within 48 hours.

---

## Questions?

- GitHub Discussions
- Matrix/Discord (links in README)

---

## License

By contributing, you agree your code will be released under the MIT License.
