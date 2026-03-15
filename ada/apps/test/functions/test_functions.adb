with Arthexis.ORM;

package body Apps.Test.Functions.Test_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS test_enabled_tests AS "
         & "SELECT test_name "
         & "FROM test_test "
         & "WHERE enabled = 1 "
         & "ORDER BY test_name;");
   end Install;

end Apps.Test.Functions.Test_Functions;
