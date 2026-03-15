with Arthexis.ORM;

package body Apps.Test.Models.Test_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS test_test ("
         & "id INTEGER PRIMARY KEY, "
         & "test_name TEXT NOT NULL UNIQUE, "
         & "enabled INTEGER NOT NULL DEFAULT 1"
         & ");");
   end Install;

end Apps.Test.Models.Test_Models;
