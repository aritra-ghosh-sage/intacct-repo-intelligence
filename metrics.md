# Calculate the repository coverage in the source repository
find . -type f -name "*.php" -o -type f -name "*.xml" -o -type f -name "*.ent" -o -type f -name "*.yaml" -o -type f -name "*.inc" -o -type f -name "*.phtml" -o -type f -name "*.cqry" -o -type f -name "*.html" -o -type f -name "*.js" -o -type f -name "*.ts" -o -type f -name "*.sql" -o -type f -name "*.cls" | wc
 -l
# Output: 24561

# Find the number of lines of code in the sqlite coverage in /app
SELECT COUNT(*)
FROM files
where path like '/app/%' and language in ('PHP', 'XML', 'ENT', 'YAML', 'INC', 'PHTML', 'CQRY', 'HTML', 'JS', 'TS', 'SQL', 'CLS');

# Output: 25143

# % coverage in the source repository
coverage=$(echo "scale=2; 25143 / 24561 * 100" | bc)
echo "Coverage: $coverage% # ~97.68% --> anything above 95% is considered good coverage

# calculate the symbol coverage
# eg pick GLAccountManager
# sql has 60 methods --> checks out with the actual GLAccountManager.cls
# has 1 class --> checks out

100% match in GLAccountManager

